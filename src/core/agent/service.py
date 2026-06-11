"""Workspace-aware application service for one complete Agent turn."""

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, RLock
from uuid import UUID, uuid4

from src.config.settings import MEMORY_ENABLED, MEMORY_EXTRACTION_ASYNC
from src.core.context.manager import AgentContextManager
from src.core.hooks.events import emit_event, record_error, set_event_context
from src.core.memory.policy import has_explicit_memory_request, memory_extraction_reason, turn_message_chars
from src.core.memory.store import PostgresMemoryStore
from src.core.streaming.events import stream_graph_events
from src.core.workspace.models import SessionContext
from src.core.workspace.repository import WorkspaceRepository
from src.core.workspace.runtime import WorkspaceRuntimeRegistry


EventCallback = Callable[[dict], None]


class SessionLockRegistry:
    def __init__(self) -> None:
        self._guard = Lock()
        self._locks: dict[UUID, RLock] = {}

    def get(self, session_id: UUID) -> RLock:
        with self._guard:
            return self._locks.setdefault(session_id, RLock())


class AgentTurnService:
    """Resolve identities, serialize a Session, and orchestrate one Agent turn."""

    def __init__(
        self,
        *,
        workspace_repository: WorkspaceRepository,
        runtime_registry: WorkspaceRuntimeRegistry,
        memory_store_factory: Callable[[], PostgresMemoryStore],
        context_manager: AgentContextManager | None = None,
        memory_enabled: bool = MEMORY_ENABLED,
        lock_registry: SessionLockRegistry | None = None,
    ) -> None:
        self.workspace_repository = workspace_repository
        self.runtime_registry = runtime_registry
        self.memory_store_factory = memory_store_factory
        self.context_manager = context_manager or AgentContextManager()
        self.memory_enabled = memory_enabled
        self.lock_registry = lock_registry or SessionLockRegistry()
        self._memory_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-memory")

    def initialize(self) -> None:
        store = self.memory_store_factory()
        try:
            store.initialize()
        finally:
            store.close()

    def run_turn(
        self,
        workspace_root: str,
        session_name: str,
        user_input: str,
        on_event: EventCallback | None = None,
        *,
        run_id: str | None = None,
    ) -> dict:
        result = {"status": "error", "run_id": run_id or uuid4().hex}
        for item in self.stream_turn(workspace_root, session_name, user_input, run_id=result["run_id"]):
            if on_event:
                on_event(item)
            if item["event"] == "done":
                result["status"] = "ok"
                result.update(item["data"])
            elif item["event"] == "error":
                result["error"] = item["data"].get("message", "Agent turn failed.")
        return result

    def stream_turn(
        self,
        workspace_root: str,
        session_name: str,
        user_input: str,
        *,
        run_id: str,
    ) -> Iterator[dict]:
        normalized = user_input.strip()
        if not normalized:
            raise ValueError("message must not be empty")
        workspace = self.workspace_repository.resolve(workspace_root)
        session, _new_session = self.workspace_repository.resolve_session(workspace, session_name)
        runtime = self.runtime_registry.get(workspace)
        with self.lock_registry.get(session.session_id):
            yield from self._stream_locked_turn(session, runtime.graph, normalized, run_id)

    def close(self) -> None:
        self._memory_executor.shutdown(wait=True, cancel_futures=False)

    def _stream_locked_turn(
        self,
        session: SessionContext,
        graph,
        user_input: str,
        run_id: str,
    ) -> Iterator[dict]:
        store = self.memory_store_factory()
        try:
            set_event_context(
                workspace_id=session.workspace.workspace_id,
                session_id=session.session_id,
                run_id=run_id,
            )
            state, turn_index = store.load_session(session)
            current_turn = turn_index + 1
            set_event_context(
                workspace_id=session.workspace.workspace_id,
                session_id=session.session_id,
                turn_index=current_turn,
                run_id=run_id,
            )
            emit_event(
                "turn_started",
                "agent_service",
                "Started workspace Agent turn.",
                {"session_name": session.session_name, "user_input_preview": user_input[:300]},
            )
            memories = store.retrieve_for_turn(
                session.workspace.workspace_id,
                user_input,
                new_session=turn_index == 0,
            )
            memory_message = store.build_memory_message(memories)
            extras = [memory_message] if memory_message else []
            memory_text = memory_message.content if memory_message else ""
            input_messages = self.context_manager.build_input_messages(
                state,
                user_input,
                extra_system_messages=extras,
            )
            for item in stream_graph_events(graph, input_messages):
                if item["event"] != "done":
                    yield item
                    continue
                final_messages = item["data"]["messages"]
                turn_messages = final_messages[len(input_messages) - 1:]
                source_ids = store.archive_turn_messages(session, current_turn, turn_messages)
                state = self.context_manager.update_after_turn(state, final_messages, memory_context=memory_text)
                store.save_session(session, state, current_turn)
                self._handle_extraction(store, session, current_turn, run_id, user_input, turn_messages, source_ids)
                emit_event("turn_finished", "agent_service", "Finished workspace Agent turn.")
                yield {
                    "event": "done",
                    "data": {
                        "run_id": run_id,
                        "status": "ok",
                        "workspace_id": str(session.workspace.workspace_id),
                        "session_id": str(session.session_id),
                        "session_name": session.session_name,
                    },
                }
        except Exception as exc:
            record_error("agent_service", "turn", exc, "Agent turn failed.", event_type="turn_failed")
            yield {"event": "error", "data": {"type": "turn_failed", "message": str(exc), "run_id": run_id}}
        finally:
            store.close()

    def _handle_extraction(
        self,
        store: PostgresMemoryStore,
        session: SessionContext,
        turn_index: int,
        run_id: str,
        user_input: str,
        messages: list,
        source_ids: list[int],
    ) -> None:
        reason = memory_extraction_reason(user_input, turn_index, messages)
        if reason in {"not_triggered", "disabled"}:
            emit_event(
                "memory_extract_skipped",
                "agent_service",
                "Long-term memory extraction skipped.",
                {"reason": reason, "turn_message_chars": turn_message_chars(messages)},
            )
            return
        if MEMORY_EXTRACTION_ASYNC and not has_explicit_memory_request(user_input):
            self._memory_executor.submit(
                self._extract_in_background,
                session,
                turn_index,
                run_id,
                messages,
                source_ids,
            )
        else:
            store.extract_and_save_memories(session, turn_index, messages, source_ids)

    def _extract_in_background(
        self,
        session: SessionContext,
        turn_index: int,
        run_id: str,
        messages: list,
        source_ids: list[int],
    ) -> None:
        set_event_context(
            workspace_id=session.workspace.workspace_id,
            session_id=session.session_id,
            turn_index=turn_index,
            run_id=run_id,
        )
        store = self.memory_store_factory()
        try:
            store.extract_and_save_memories(session, turn_index, messages, source_ids)
        except Exception as exc:
            record_error(
                "agent_service",
                "memory_background_extract",
                exc,
                "Background long-term memory extraction failed.",
                event_type="memory_failed",
            )
        finally:
            store.close()
