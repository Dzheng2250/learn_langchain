"""Factory for non-recursive workspace-bound sub-agents."""

import os
from typing import Annotated

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import InjectedState, tools_condition

from src.config.settings import MODEL, SUBAGENT_CONTEXT_MESSAGE_LIMIT, SUBAGENT_MAX_STEPS, SUBAGENT_RESULT_LIMIT
from src.core.common.debug import format_message
from src.core.tools.observed import ObservedToolNode


def create_delegate_tool(base_tools: list):
    """Create a delegate tool whose sub-agent cannot recursively delegate."""
    llm = ChatOpenAI(
        model=MODEL,
        api_key=os.getenv("ALIYUN_API_KEY"),
        base_url=os.getenv("ALIYUN_BASE_URL"),
        temperature=0,
        streaming=False,
    ).bind_tools(base_tools)

    def subagent_node(state: MessagesState) -> dict:
        response = llm.invoke(
            [
                SystemMessage(
                    content=(
                        "You are a focused non-recursive coding sub-agent. Use the available "
                        "workspace-bound tools and return compact findings with evidence. "
                        "You cannot delegate to another agent."
                    )
                ),
                *state["messages"],
            ]
        )
        return {"messages": [response]}

    builder = StateGraph(MessagesState)
    builder.add_node("subagent", subagent_node)
    builder.add_node("tools", ObservedToolNode(base_tools, event_source="subagent_tool_node"))
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
        prompt = (
            f"Task:\n{task}\n\nExtra context:\n{context or '(none)'}\n\n"
            f"Recent parent context:\n{parent_context or '(none)'}"
        )
        try:
            result = graph.invoke(
                {"messages": [HumanMessage(content=prompt)]},
                config={"recursion_limit": SUBAGENT_MAX_STEPS},
            )
        except GraphRecursionError:
            return f"Sub-agent exceeded its {SUBAGENT_MAX_STEPS}-step limit."
        return str(result["messages"][-1].content)[:SUBAGENT_RESULT_LIMIT]

    return delegate_to_subagent
