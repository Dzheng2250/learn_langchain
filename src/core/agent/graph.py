import os

from dotenv import load_dotenv
from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import tools_condition

from src.core.common.debug import debug_print, format_message, format_messages
from src.config.settings import FILE_READ_CHUNK_LINES, MODEL
from src.core.hooks.events import emit_event, record_error
from src.core.subagent.graph import delegate_to_subagent
from src.core.tools.observed import ObservedToolNode
from src.core.tools.registry import parent_base_tools, skill_store


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
