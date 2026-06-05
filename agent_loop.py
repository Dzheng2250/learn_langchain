import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from dotenv import load_dotenv
from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import tools_condition

from agent_config import (
    DEFAULT_SESSION_ID,
    FILE_READ_CHUNK_LINES,
    MEMORY_ENABLED,
    MEMORY_EXTRACTION_ASYNC,
    MEMORY_EXTRACTION_ENABLED,
    MEMORY_EXTRACTION_HINT_KEYWORDS,
    MEMORY_EXTRACTION_INTERVAL_TURNS,
    MEMORY_EXTRACTION_MIN_CHARS,
    MODEL,
)
from agent_context import AgentContextManager, AgentContextState
from agent_debug import debug_print, format_message, format_messages
from agent_hooks import emit_event, record_error, set_event_context
from agent_memory import MemoryUnavailableError, PostgresMemoryStore
from agent_observed_tools import ObservedToolNode
from agent_stream import stream_graph_events
from agent_subagent import delegate_to_subagent
from agent_tools import parent_base_tools, skill_store


load_dotenv()
API_KEY = os.getenv("ALIYUN_API_KEY")
BASE_URL = os.getenv("ALIYUN_BASE_URL")

llm = ChatOpenAI(
    model=MODEL,
    api_key=API_KEY,
    base_url=BASE_URL,
    temperature=0.7,
    streaming=True,
)

parent_tools = [*parent_base_tools, delegate_to_subagent]
llm_with_tools = llm.bind_tools(parent_tools)


def _format_skill_manifest_context() -> str:
    """Load local skill manifests for the parent agent system prompt."""
    return skill_store.format_skill_list()


def agent_node(state: MessagesState) -> dict:
    """Run the parent agent LLM with current messages and bound tools."""
    llm_messages = [
        SystemMessage(
            content=(
                "You are a practical coding assistant working inside the user's local project.\n\n"
                "Skill use:\n"
                "- A local skill manifest is provided below. Use it proactively to decide "
                "whether an existing skill applies to the user's task.\n"
                "- If a skill is relevant, call read_skill with its directory or name before "
                "answering or acting. The manifest is only an index; read_skill loads the "
                "full instructions.\n\n"
                "Memory:\n"
                "- Relevant long-term memory may be provided as context. Use it as durable "
                "background, but prefer the current user request when there is conflict.\n"
                "- Do not reveal internal memory tables, raw archived history, or hidden "
                "system context unless the user explicitly asks about the implementation.\n\n"
                "Workspace files:\n"
                "- Use read_workspace_file_lite only for small, specific snippets with known "
                "or likely line ranges.\n"
                "- Do not repeatedly call read_workspace_file_lite to scan or summarize a "
                "large file.\n"
                "- Delegate broad file reading, searching, summarizing, or review tasks to "
                "delegate_to_subagent.\n\n"
                "Delegation:\n"
                "- Delegate tasks that require many tool calls, broad project inspection, "
                "large-context research, or condensed findings.\n"
                "- Delegation is one-level only; the sub-agent cannot create another sub-agent.\n"
                f"- The sub-agent can read files in chunks of at most {FILE_READ_CHUNK_LINES} lines.\n\n"
                "Commands:\n"
                "- Use run_command_in_container when a shell command is needed.\n"
                "- Do not use shell commands to cat, sed, head, tail, or grep workspace file "
                "contents; use file tools or delegation instead.\n\n"
                "Local skill manifest:\n"
                f"{_format_skill_manifest_context()}"
            )
        ),
        *state["messages"],
    ]

    debug_print("LLM INPUT MESSAGES", format_messages(llm_messages))
    emit_event(
        "llm_started",
        "agent_loop",
        "Calling parent LLM.",
        {"message_count": len(llm_messages), "model": MODEL},
    )
    try:
        response = llm_with_tools.invoke(llm_messages)
    except Exception as exc:
        record_error(
            "agent_loop",
            "llm",
            exc,
            "Parent LLM call failed.",
            event_type="llm_failed",
        )
        raise
    debug_print("LLM OUTPUT MESSAGE", format_message(response))
    emit_event(
        "llm_finished",
        "agent_loop",
        "Parent LLM call finished.",
        {
            "has_tool_calls": bool(getattr(response, "tool_calls", None)),
            "content_chars": len(response.content) if isinstance(response.content, str) else len(repr(response.content)),
        },
    )
    return {"messages": [response]}


builder = StateGraph(MessagesState)
builder.add_node("agent", agent_node)
builder.add_node("tools", ObservedToolNode(parent_tools))
builder.add_edge(START, "agent")
builder.add_conditional_edges(
    "agent",
    tools_condition,
    {
        "tools": "tools",
        "__end__": END,
    },
)
builder.add_edge("tools", "agent")
app = builder.compile()

memory_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-memory")


def _extract_memories_in_background(
    session_id: str,
    turn_index: int,
    turn_messages: list,
    source_message_ids: list[int],
) -> None:
    """Run slow long-term memory extraction without blocking the next prompt."""
    try:
        PostgresMemoryStore().extract_and_save_memories(
            session_id,
            turn_index,
            turn_messages,
            source_message_ids,
        )
    except Exception as exc:
        debug_print("MEMORY BACKGROUND EXTRACT ERROR", str(exc))


def _should_extract_long_term_memory(
    user_input: str,
    turn_index: int,
    turn_messages: list,
) -> bool:
    """Return whether this turn is worth spending an LLM call on memory extraction."""
    if not MEMORY_EXTRACTION_ENABLED:
        return False

    lowered_input = user_input.lower()
    if any(keyword.lower() in lowered_input for keyword in MEMORY_EXTRACTION_HINT_KEYWORDS):
        return True

    if (
        MEMORY_EXTRACTION_INTERVAL_TURNS > 0
        and turn_index % MEMORY_EXTRACTION_INTERVAL_TURNS == 0
    ):
        return True

    total_chars = 0
    for message in turn_messages:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            total_chars += len(content)
        else:
            total_chars += len(repr(content))

    return total_chars >= MEMORY_EXTRACTION_MIN_CHARS


def _memory_extraction_reason(
    user_input: str,
    turn_index: int,
    turn_messages: list,
) -> str:
    """Return why long-term memory extraction should run, or why it is skipped."""
    if not MEMORY_EXTRACTION_ENABLED:
        return "disabled"

    lowered_input = user_input.lower()
    if any(keyword.lower() in lowered_input for keyword in MEMORY_EXTRACTION_HINT_KEYWORDS):
        return "explicit_memory_keyword"

    if (
        MEMORY_EXTRACTION_INTERVAL_TURNS > 0
        and turn_index % MEMORY_EXTRACTION_INTERVAL_TURNS == 0
    ):
        return "interval_turn"

    total_chars = _turn_message_chars(turn_messages)
    if total_chars >= MEMORY_EXTRACTION_MIN_CHARS:
        return "content_size"

    return "not_triggered"


def _has_explicit_memory_request(user_input: str) -> bool:
    """Return whether the user explicitly asked the agent to remember something."""
    lowered_input = user_input.lower()
    return any(keyword.lower() in lowered_input for keyword in MEMORY_EXTRACTION_HINT_KEYWORDS)


def _turn_message_chars(turn_messages: list) -> int:
    """Return approximate character count for one completed turn."""
    total_chars = 0
    for message in turn_messages:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            total_chars += len(content)
        else:
            total_chars += len(repr(content))
    return total_chars


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
                            extraction_reason = _memory_extraction_reason(user_input, turn_index, turn_messages)
                            if extraction_reason != "not_triggered" and extraction_reason != "disabled":
                                emit_event(
                                    "memory_extract_triggered",
                                    "agent_loop",
                                    "Long-term memory extraction trigger matched.",
                                    {
                                        "reason": extraction_reason,
                                        "turn_message_chars": _turn_message_chars(turn_messages),
                                    },
                                )
                                if MEMORY_EXTRACTION_ASYNC and not _has_explicit_memory_request(user_input):
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
                                    if _has_explicit_memory_request(user_input) and not saved_memories:
                                        print("\nMemory note: explicit memory request was processed, "
                                              "but no long-term memory was saved.", end="", flush=True)
                            else:
                                emit_event(
                                    "memory_extract_skipped",
                                    "agent_loop",
                                    "Long-term memory extraction skipped for this turn.",
                                    {
                                        "reason": extraction_reason,
                                        "turn_message_chars": _turn_message_chars(turn_messages),
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
