import os
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

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
from agent_memory import MemoryUnavailableError, PostgresMemoryStore
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
    response = llm_with_tools.invoke(llm_messages)
    debug_print("LLM OUTPUT MESSAGE", format_message(response))
    return {"messages": [response]}


builder = StateGraph(MessagesState)
builder.add_node("agent", agent_node)
builder.add_node("tools", ToolNode(parent_tools))
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


def _has_explicit_memory_request(user_input: str) -> bool:
    """Return whether the user explicitly asked the agent to remember something."""
    lowered_input = user_input.lower()
    return any(keyword.lower() in lowered_input for keyword in MEMORY_EXTRACTION_HINT_KEYWORDS)


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

    while True:
        user_input = input("\nYou: ").strip()
        if not user_input:
            continue

        if user_input.lower() in {"exit", "quit"}:
            print("Bye.")
            break

        print("AI: ", end="", flush=True)

        extra_system_messages = []
        if memory_store:
            memories = memory_store.retrieve_memories(user_input)
            memory_message = memory_store.build_memory_message(memories)
            if memory_message:
                extra_system_messages.append(memory_message)

        input_messages = context_manager.build_input_messages(
            context_state,
            user_input,
            extra_system_messages=extra_system_messages,
        )
        current_turn_index = turn_index + 1

        for item in stream_graph_events(app, input_messages):
            if item["event"] == "token":
                print(item["data"]["content"], end="", flush=True)
            elif item["event"] == "error":
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
                )
                turn_index = current_turn_index

                if memory_store:
                    memory_store.save_session(DEFAULT_SESSION_ID, context_state, turn_index)
                    if _should_extract_long_term_memory(user_input, turn_index, turn_messages):
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

        print()


if __name__ == "__main__":
    run_agent_loop()
