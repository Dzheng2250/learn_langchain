"""Factory for workspace-bound parent Agent graphs."""

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import tools_condition

from src.config.settings import FILE_READ_CHUNK_LINES
from src.core.common.debug import debug_print, format_message, format_messages
from src.core.telemetry import emit_event, record_error
from src.core.llm.contracts import LlmPurpose, ModelProvider
from src.core.prompts import build_parent_system_prompt
from src.core.tasks.context import ToolExecutionContext
from src.core.tools.observed import ObservedToolNode


def create_parent_graph(
    parent_tools: list,
    skill_manifest: str,
    model_provider: ModelProvider,
    *,
    checkpointer=None,
    risk_by_name=None,
    tool_pipeline=None,
):
    """Create one compiled graph permanently bound to a WorkspaceRuntime."""
    llm_with_tools = model_provider.create_chat_model(
        LlmPurpose.PARENT_AGENT,
        temperature=0.7,
        streaming=True,
        tools=parent_tools,
    )

    def agent_node(state: MessagesState, config: RunnableConfig) -> dict:
        """Call the parent LLM and propagate LangGraph streaming callbacks."""
        llm_messages = [
            SystemMessage(
                content=build_parent_system_prompt(
                    skill_manifest,
                    FILE_READ_CHUNK_LINES,
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
            response = llm_with_tools.invoke(llm_messages, config=config)
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

    builder = StateGraph(MessagesState, context_schema=ToolExecutionContext)
    builder.add_node("agent", agent_node)
    builder.add_node(
        "tools",
        ObservedToolNode(
            parent_tools,
            risk_by_name=risk_by_name,
            pipeline=tool_pipeline,
        ),
    )
    # The graph is the AgentLoop: LLM output without tool calls terminates;
    # tool calls execute centrally, append ToolMessages, then return to LLM.
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition, {"tools": "tools", "__end__": END})
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=checkpointer)
