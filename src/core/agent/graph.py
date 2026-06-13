"""Factory for workspace-bound parent Agent graphs."""

from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import tools_condition

from src.config.settings import FILE_READ_CHUNK_LINES
from src.core.common.debug import debug_print, format_message, format_messages
from src.core.telemetry import emit_event, record_error
from src.core.llm.provider import LlmPurpose, ModelProvider, OpenAICompatibleProvider
from src.core.tools.observed import ObservedToolNode


def create_parent_graph(
    parent_tools: list,
    skill_manifest: str,
    model_provider: ModelProvider | None = None,
    *,
    checkpointer=None,
    risk_by_name=None,
):
    """Create one compiled graph permanently bound to a WorkspaceRuntime."""
    provider = model_provider or OpenAICompatibleProvider()
    llm_with_tools = provider.create_chat_model(
        LlmPurpose.PARENT_AGENT,
        temperature=0.7,
        streaming=True,
        tools=parent_tools,
    )

    def agent_node(state: MessagesState) -> dict:
        """Call the parent LLM with system policy and current graph messages."""
        llm_messages = [
            SystemMessage(
                content=(
                    "You are a practical coding assistant working inside one strictly isolated "
                    "local workspace. Never claim access outside that workspace.\n\n"
                    "Use relevant long-term memory as background, but prefer the current request. "
                    "When the user asks you to remember something, do not claim it is already "
                    "saved; durable memory extraction is queued after the response and reported "
                    "separately by the client. "
                    "Use read_workspace_file_lite only for targeted snippets and delegate broad "
                    "inspection to delegate_to_subagent. Use run_command_in_container for commands. "
                    f"The sub-agent reads chunks of at most {FILE_READ_CHUNK_LINES} lines.\n\n"
                    f"Local skill manifest:\n{skill_manifest}"
                )
            ),
            *state["messages"],
        ]
        debug_print("LLM INPUT MESSAGES", format_messages(llm_messages))
        emit_event(
            "llm_started",
            "agent_loop",
            "Calling parent LLM.",
            {"purpose": LlmPurpose.PARENT_AGENT.value},
        )
        try:
            response = llm_with_tools.invoke(llm_messages)
        except Exception as exc:
            record_error("agent_loop", "llm", exc, "Parent LLM call failed.", event_type="llm_failed")
            raise
        debug_print("LLM OUTPUT MESSAGE", format_message(response))
        emit_event(
            "llm_finished",
            "agent_loop",
            "Parent LLM call finished.",
            {"has_tool_calls": bool(getattr(response, "tool_calls", None))},
        )
        return {"messages": [response]}

    builder = StateGraph(MessagesState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ObservedToolNode(parent_tools, risk_by_name=risk_by_name))
    # The graph is the AgentLoop: LLM output without tool calls terminates;
    # tool calls execute centrally, append ToolMessages, then return to LLM.
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition, {"tools": "tools", "__end__": END})
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=checkpointer)
