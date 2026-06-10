from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from src.core.agent.graph import app
from src.core.common.debug import debug_print
from src.core.config.settings import (
    DEFAULT_SESSION_ID,
    MEMORY_ENABLED,
    MEMORY_EXTRACTION_ASYNC,
)
from src.core.context.manager import AgentContextManager, AgentContextState
from src.core.hooks.events import emit_event, record_error, set_event_context
from src.core.memory.policy import (
    has_explicit_memory_request,
    memory_extraction_reason,
    turn_message_chars,
)
from src.core.memory.store import MemoryUnavailableError, PostgresMemoryStore
from src.core.streaming.events import stream_graph_events


memory_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-memory")


def _extract_memories_in_background(
    session_id: str,
    turn_index: int,
    turn_messages: list,
    source_message_ids: list[int],
) -> None:
    """Run slow long-term memory extraction without blocking the next prompt."""
    store = None
    try:
        store = PostgresMemoryStore()
        store.extract_and_save_memories(
            session_id,
            turn_index,
            turn_messages,
            source_message_ids,
        )
    except Exception as exc:
        debug_print("MEMORY BACKGROUND EXTRACT ERROR", str(exc))
        record_error(
            "agent_runtime",
            "memory_background_extract",
            exc,
            "Background long-term memory extraction failed.",
            {"session_id": session_id, "turn_index": turn_index},
            event_type="memory_failed",
        )
    finally:
        if store is not None:
            store.close()


def run_agent_loop() -> None:
    """Run a minimal command-line agent loop."""
    context_manager = AgentContextManager()
    memory_store = None
    turn_index = 0

    if MEMORY_ENABLED:
        try:
            memory_store = PostgresMemoryStore()
            memory_store.initialize()
            context_state, turn_index = memory_store.load_session(DEFAULT_SESSION_ID)
        except MemoryUnavailableError as exc:
            print(f"Memory initialization failed: {exc}")
            return
    else:
        context_state = AgentContextState()

    print("Agent started. Type 'exit' or 'quit' to stop.")

    try:
        while True:
            user_input = input("\nYou: ").strip()
            if not user_input:
                continue

            if user_input.lower() in {"exit", "quit"}:
                print("Bye.")
                break

            print("AI: ", end="", flush=True)

            try:
                current_turn_index = turn_index + 1
                run_id = str(uuid4())
                set_event_context(DEFAULT_SESSION_ID, current_turn_index, run_id)
                emit_event(
                    "turn_started",
                    "agent_loop",
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

                input_messages = context_manager.build_input_messages(
                    context_state,
                    user_input,
                    extra_system_messages=extra_system_messages,
                )
                emit_event(
                    "context_loaded",
                    "agent_loop",
                    "Built input messages for agent turn.",
                    {
                        "input_message_count": len(input_messages),
                        "extra_system_messages": len(extra_system_messages),
                        "recent_messages": len(context_state.recent_messages),
                        "has_summary": bool(context_state.summary),
                    },
                )

                for item in stream_graph_events(app, input_messages):
                    if item["event"] == "token":
                        print(item["data"]["content"], end="", flush=True)
                    elif item["event"] == "error":
                        record_error(
                            "agent_loop",
                            "turn",
                            RuntimeError(item["data"].get("message", "")),
                            "Agent turn failed.",
                            item["data"],
                            event_type="turn_failed",
                        )
                        print(f"\n{item['data']['message']}", end="", flush=True)
                    elif item["event"] == "done":
                        final_messages = item["data"]["messages"]
                        turn_messages = final_messages[len(input_messages) - 1:]
                        source_message_ids = []

                        if memory_store:
                            source_message_ids = memory_store.archive_turn_messages(
                                DEFAULT_SESSION_ID,
                                current_turn_index,
                                turn_messages,
                            )

                        context_state = context_manager.update_after_turn(
                            context_state,
                            final_messages,
                            memory_context=memory_context_text,
                        )
                        turn_index = current_turn_index

                        if memory_store:
                            memory_store.save_session(DEFAULT_SESSION_ID, context_state, turn_index)
                            extraction_reason = memory_extraction_reason(user_input, turn_index, turn_messages)
                            if extraction_reason != "not_triggered" and extraction_reason != "disabled":
                                emit_event(
                                    "memory_extract_triggered",
                                    "agent_loop",
                                    "Long-term memory extraction trigger matched.",
                                    {
                                        "reason": extraction_reason,
                                        "turn_message_chars": turn_message_chars(turn_messages),
                                    },
                                )
                                if MEMORY_EXTRACTION_ASYNC and not has_explicit_memory_request(user_input):
                                    memory_executor.submit(
                                        _extract_memories_in_background,
                                        DEFAULT_SESSION_ID,
                                        turn_index,
                                        turn_messages,
                                        source_message_ids,
                                    )
                                else:
                                    saved_memories = memory_store.extract_and_save_memories(
                                        DEFAULT_SESSION_ID,
                                        turn_index,
                                        turn_messages,
                                        source_message_ids,
                                    )
                                    if has_explicit_memory_request(user_input) and not saved_memories:
                                        print("\nMemory note: explicit memory request was processed, "
                                              "but no long-term memory was saved.", end="", flush=True)
                            else:
                                emit_event(
                                    "memory_extract_skipped",
                                    "agent_loop",
                                    "Long-term memory extraction skipped for this turn.",
                                    {
                                        "reason": extraction_reason,
                                        "turn_message_chars": turn_message_chars(turn_messages),
                                    },
                                )

                        emit_event(
                            "turn_finished",
                            "agent_loop",
                            "Finished agent turn.",
                            {
                                "final_message_count": len(final_messages),
                                "turn_message_count": len(turn_messages),
                            },
                        )

                print()
            except Exception as exc:
                print(f"\nTurn error: {exc}", flush=True)
                record_error(
                    "agent_loop",
                    "turn",
                    exc,
                    "Agent turn failed with unhandled exception.",
                    event_type="turn_failed",
                )
    finally:
        if memory_store:
            memory_store.close()


if __name__ == "__main__":
    run_agent_loop()
