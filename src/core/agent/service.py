"""Application service for executing one complete agent turn."""

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, RLock
from uuid import uuid4

from src.core.agent.graph import app
from src.core.common.debug import debug_print
from src.config.settings import MEMORY_ENABLED, MEMORY_EXTRACTION_ASYNC
from src.core.context.manager import AgentContextManager
from src.core.hooks.events import emit_event, record_error, set_event_context
from src.core.memory.policy import (
    has_explicit_memory_request,
    memory_extraction_reason,
    turn_message_chars,
)
from src.core.memory.store import PostgresMemoryStore
from src.core.streaming.events import stream_graph_events


EventCallback = Callable[[dict], None]


class SessionLockRegistry:
    """Provide one execution lock per session."""

    def __init__(self) -> None:
        self._guard = Lock()
        self._locks: dict[str, RLock] = {}

    def get(self, session_id: str) -> RLock:
        with self._guard:
            return self._locks.setdefault(session_id, RLock())


class AgentTurnService:
    """Execute agent turns without depending on a CLI or transport."""

    def __init__(
        self,
        *,
        graph=app,
        context_manager: AgentContextManager | None = None,
        memory_store_factory: Callable[[], PostgresMemoryStore] = PostgresMemoryStore,
        memory_enabled: bool = MEMORY_ENABLED,
        lock_registry: SessionLockRegistry | None = None,
    ) -> None:
        self.graph = graph
        self.context_manager = context_manager or AgentContextManager()
        self.memory_store_factory = memory_store_factory
        self.memory_enabled = memory_enabled
        self.lock_registry = lock_registry or SessionLockRegistry()
        self._memory_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-memory")

    def initialize(self) -> None:
        """Initialize durable dependencies used by the service."""
        if not self.memory_enabled:
            return
        store = self.memory_store_factory()
        try:
            store.initialize()
        finally:
            store.close()

    def stream_turn(
        self,
        session_id: str,
        user_input: str,
        *,
        run_id: str | None = None,
    ) -> Iterator[dict]:
        """Yield events while executing one serialized session turn."""
        normalized_input = user_input.strip()
        if not normalized_input:
            raise ValueError("message must not be empty")

        effective_run_id = run_id or str(uuid4())
        with self.lock_registry.get(session_id):
            yield from self._stream_locked_turn(session_id, normalized_input, effective_run_id)

    def run_turn(
        self,
        session_id: str,
        user_input: str,
        on_event: EventCallback | None = None,
        *,
        run_id: str | None = None,
    ) -> dict:
        """Execute one turn and optionally forward each stream event."""
        result = {"status": "error", "run_id": run_id or str(uuid4())}
        for item in self.stream_turn(session_id, user_input, run_id=result["run_id"]):
            if on_event is not None:
                on_event(item)
            if item["event"] == "done":
                result["status"] = "ok"
            elif item["event"] == "error":
                result["error"] = item["data"].get("message", "Agent turn failed.")
        return result

    def close(self) -> None:
        """Stop service-owned background executors."""
        self._memory_executor.shutdown(wait=False, cancel_futures=False)

    def _stream_locked_turn(self, session_id: str, user_input: str, run_id: str) -> Iterator[dict]:
        memory_store = None
        context_state = None
        turn_index = 0
        try:
            if self.memory_enabled:
                memory_store = self.memory_store_factory()
                context_state, turn_index = memory_store.load_session(session_id)
            else:
                from src.core.context.models import AgentContextState

                context_state = AgentContextState()

            current_turn_index = turn_index + 1
            set_event_context(session_id, current_turn_index, run_id)
            emit_event(
                "turn_started",
                "agent_service",
                "Started agent turn.",
                {"user_input_preview": user_input[:300]},
            )

            extra_system_messages = []
            memory_context_text = ""
            if memory_store:
                memories = memory_store.retrieve_memories(user_input)
                memory_message = memory_store.build_memory_message(memories)
                if memory_message:
                    extra_system_messages.append(memory_message)
                    memory_context_text = memory_message.content

            input_messages = self.context_manager.build_input_messages(
                context_state,
                user_input,
                extra_system_messages=extra_system_messages,
            )
            emit_event(
                "context_loaded",
                "agent_service",
                "Built input messages for agent turn.",
                {
                    "input_message_count": len(input_messages),
                    "extra_system_messages": len(extra_system_messages),
                    "recent_messages": len(context_state.recent_messages),
                    "has_summary": bool(context_state.summary),
                },
            )

            for item in stream_graph_events(self.graph, input_messages):
                if item["event"] != "done":
                    yield item
                    continue

                final_messages = item["data"]["messages"]
                turn_messages = final_messages[len(input_messages) - 1:]
                source_message_ids = []
                if memory_store:
                    source_message_ids = memory_store.archive_turn_messages(
                        session_id,
                        current_turn_index,
                        turn_messages,
                    )

                context_state = self.context_manager.update_after_turn(
                    context_state,
                    final_messages,
                    memory_context=memory_context_text,
                )
                if memory_store:
                    memory_store.save_session(session_id, context_state, current_turn_index)
                    self._handle_memory_extraction(
                        memory_store,
                        session_id,
                        current_turn_index,
                        user_input,
                        turn_messages,
                        source_message_ids,
                    )

                emit_event(
                    "turn_finished",
                    "agent_service",
                    "Finished agent turn.",
                    {
                        "final_message_count": len(final_messages),
                        "turn_message_count": len(turn_messages),
                    },
                )
                yield {"event": "done", "data": {"run_id": run_id, "status": "ok"}}
        except Exception as exc:
            record_error(
                "agent_service",
                "turn",
                exc,
                "Agent turn failed with unhandled exception.",
                event_type="turn_failed",
            )
            yield {
                "event": "error",
                "data": {"type": "turn_failed", "message": str(exc), "run_id": run_id},
            }
        finally:
            if memory_store is not None:
                memory_store.close()

    def _handle_memory_extraction(
        self,
        store: PostgresMemoryStore,
        session_id: str,
        turn_index: int,
        user_input: str,
        turn_messages: list,
        source_message_ids: list[int],
    ) -> None:
        reason = memory_extraction_reason(user_input, turn_index, turn_messages)
        if reason in {"not_triggered", "disabled"}:
            emit_event(
                "memory_extract_skipped",
                "agent_service",
                "Long-term memory extraction skipped for this turn.",
                {"reason": reason, "turn_message_chars": turn_message_chars(turn_messages)},
            )
            return

        emit_event(
            "memory_extract_triggered",
            "agent_service",
            "Long-term memory extraction trigger matched.",
            {"reason": reason, "turn_message_chars": turn_message_chars(turn_messages)},
        )
        if MEMORY_EXTRACTION_ASYNC and not has_explicit_memory_request(user_input):
            self._memory_executor.submit(
                self._extract_memories_in_background,
                session_id,
                turn_index,
                turn_messages,
                source_message_ids,
            )
            return

        store.extract_and_save_memories(session_id, turn_index, turn_messages, source_message_ids)

    def _extract_memories_in_background(
        self,
        session_id: str,
        turn_index: int,
        turn_messages: list,
        source_message_ids: list[int],
    ) -> None:
        store = None
        try:
            store = self.memory_store_factory()
            store.extract_and_save_memories(session_id, turn_index, turn_messages, source_message_ids)
        except Exception as exc:
            debug_print("MEMORY BACKGROUND EXTRACT ERROR", str(exc))
            record_error(
                "agent_service",
                "memory_background_extract",
                exc,
                "Background long-term memory extraction failed.",
                {"session_id": session_id, "turn_index": turn_index},
                event_type="memory_failed",
            )
        finally:
            if store is not None:
                store.close()
