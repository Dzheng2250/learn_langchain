"""Factory for workspace-bound parent Agent graphs."""

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import tools_condition

from src.config.settings import FILE_READ_CHUNK_LINES
from src.core.common.debug import debug_print, format_message, format_messages
from src.core.telemetry import emit_event, record_error
from src.core.llm.contracts import LlmPurpose, ModelProvider
from src.core.llm.completion import ensure_complete_response, response_stop_reason
from src.core.llm.usage import context_tokens, has_context_usage, message_usage
from src.core.prompts import build_parent_system_prompt
from src.core.tasks.context import ToolExecutionContext
from src.core.tools.observed import LedgerBackedToolNode
from src.core.agent.context_guard import (
    AgentGraphState,
    InTurnContextGuard,
    latest_tool_results,
)


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

    parent_system_message = SystemMessage(
        content=build_parent_system_prompt(
            skill_manifest,
            FILE_READ_CHUNK_LINES,
        )
    )
    context_guard = InTurnContextGuard(
        model_provider,
        system_message=parent_system_message,
        tools=parent_tools,
    )

    def agent_node(state: AgentGraphState, config: RunnableConfig) -> dict:
        """Call the parent LLM and propagate LangGraph streaming callbacks."""
        llm_messages = [
            parent_system_message,
            *(
                [SystemMessage(content=(
                    "Current Turn working summary. Treat it as prior execution "
                    "state, not as a new user request:\n"
                    f"{state.get('working_summary', '')}"
                ))]
                if state.get("working_summary") else []
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
        ensure_complete_response(response)
        usage = message_usage(response)
        exact_context_tokens = context_tokens(usage)
        debug_print("LLM OUTPUT MESSAGE", format_message(response))
        emit_event(
            "llm_finished",
            "agent_loop",
            "Parent LLM call finished.",
            {
                "has_tool_calls": bool(getattr(response, "tool_calls", None)),
                "stop_reason": response_stop_reason(response),
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "context_tokens": exact_context_tokens,
            },
        )
        update = {
            "messages": [response],
            "turn_journal": [response],
            "context_usage_available": has_context_usage(usage),
            "llm_usage_generation": int(state.get("llm_usage_generation") or 0) + 1,
        }
        if has_context_usage(usage):
            update.update(
                {
                    "context_input_tokens": int(usage.get("input_tokens") or 0),
                    "context_output_tokens": int(usage.get("output_tokens") or 0),
                    "context_tokens": exact_context_tokens,
                }
            )
        return update

    def journal_tool_results(state: AgentGraphState) -> dict:
        """Mirror completed tool results into the append-only Turn journal."""
        return {"turn_journal": latest_tool_results(state)}

    def route_context_guard(state: AgentGraphState) -> str:
        """Checkpoint a completed compaction before validating again."""
        return "retry" if state.get("context_guard_retry") else "ready"

    builder = StateGraph(AgentGraphState, context_schema=ToolExecutionContext)
    builder.add_node("context_guard", context_guard)
    builder.add_node("agent", agent_node)
    builder.add_node(
        "tools",
        LedgerBackedToolNode(
            parent_tools,
            specs={
                name: spec
                for name, spec in getattr(tool_pipeline, "specs", {}).items()
            },
            risk_by_name=risk_by_name,
            pipeline=tool_pipeline,
        ),
    )
    builder.add_node("journal_tools", journal_tool_results)
    # The graph is the AgentLoop: LLM output without tool calls terminates;
    # tool calls execute centrally, append ToolMessages, then return to LLM.
    builder.add_edge(START, "context_guard")
    builder.add_conditional_edges(
        "context_guard",
        route_context_guard,
        {"retry": "context_guard", "ready": "agent"},
    )
    builder.add_conditional_edges("agent", tools_condition, {"tools": "tools", "__end__": END})
    builder.add_edge("tools", "journal_tools")
    builder.add_edge("journal_tools", "context_guard")
    return builder.compile(checkpointer=checkpointer)
