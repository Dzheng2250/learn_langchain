"""Factory for non-recursive workspace-bound sub-agents."""

from typing import Annotated

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import InjectedState, tools_condition

from src.config.settings import SUBAGENT_CONTEXT_MESSAGE_LIMIT, SUBAGENT_MAX_STEPS, SUBAGENT_RESULT_LIMIT
from src.core.common.content import message_content_text
from src.core.common.debug import format_message
from src.core.llm.contracts import LlmPurpose, ModelProvider
from src.core.prompts import SUBAGENT_SYSTEM_PROMPT, build_subagent_task_prompt
from src.core.tools.observed import ObservedToolNode


def create_delegate_tool(
    base_tools: list,
    model_provider: ModelProvider,
    *,
    max_steps: int = SUBAGENT_MAX_STEPS,
    risk_by_name=None,
):
    """Create a delegate tool whose sub-agent cannot recursively delegate."""
    provider = model_provider
    llm = provider.create_chat_model(
        LlmPurpose.SUBAGENT,
        temperature=0,
        streaming=False,
        tools=base_tools,
    )

    def subagent_node(state: MessagesState) -> dict:
        """Call the non-recursive sub-agent LLM with its bounded tool view."""
        response = llm.invoke(
            [
                SystemMessage(content=SUBAGENT_SYSTEM_PROMPT),
                *state["messages"],
            ]
        )
        return {"messages": [response]}

    builder = StateGraph(MessagesState)
    builder.add_node("subagent", subagent_node)
    builder.add_node(
        "tools",
        ObservedToolNode(
            base_tools,
            event_source="subagent_tool_node",
            risk_by_name=risk_by_name,
        ),
    )
    builder.add_edge(START, "subagent")
    builder.add_conditional_edges("subagent", tools_condition, {"tools": "tools", "__end__": END})
    builder.add_edge("tools", "subagent")
    graph = builder.compile()

    @tool
    def delegate_to_subagent(
        task: str,
        context: str = "",
        state: Annotated[dict, InjectedState()] = None,
    ) -> str:
        """Delegate bounded workspace research to a non-recursive sub-agent."""
        messages = (state or {}).get("messages", [])[-SUBAGENT_CONTEXT_MESSAGE_LIMIT:]
        parent_context = "\n\n".join(format_message(message) for message in messages)
        prompt = build_subagent_task_prompt(task, context, parent_context)
        try:
            result = graph.invoke(
                {"messages": [HumanMessage(content=prompt)]},
                config={"recursion_limit": max_steps},
            )
        except GraphRecursionError:
            return f"Sub-agent exceeded its {max_steps}-step limit."
        return message_content_text(result["messages"][-1])[:SUBAGENT_RESULT_LIMIT]

    return delegate_to_subagent
