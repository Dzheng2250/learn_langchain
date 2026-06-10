import os
from typing import Annotated

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import InjectedState, ToolNode, tools_condition

from src.core.common.debug import debug_print, format_message, format_messages
from src.core.config.settings import MODEL, SUBAGENT_CONTEXT_MESSAGE_LIMIT, SUBAGENT_MAX_STEPS, SUBAGENT_RESULT_LIMIT
from src.core.tools.registry import base_tools


load_dotenv()

subagent_llm = ChatOpenAI(
    model=MODEL,
    api_key=os.getenv("ALIYUN_API_KEY"),
    base_url=os.getenv("ALIYUN_BASE_URL"),
    temperature=0,
    streaming=False,
)
subagent_llm_with_tools = subagent_llm.bind_tools(base_tools)


def _format_parent_context(state: dict | None) -> str:
    """Compress the parent graph state into a small context block."""
    if not state:
        return "No parent context was injected."

    messages = state.get("messages", [])
    recent_messages = messages[-SUBAGENT_CONTEXT_MESSAGE_LIMIT:]

    if not recent_messages:
        return "The parent conversation has no prior messages."

    return "\n\n".join(
        f"[parent message {index}]\n{format_message(message)}"
        for index, message in enumerate(recent_messages, start=1)
    )


def _subagent_node(state: MessagesState) -> dict:
    """Run one reasoning step for the sub-agent."""
    llm_messages = [
        SystemMessage(
            content=(
                "You are a focused non-recursive sub-agent. Complete the delegated task "
                "with the available tools, then return a compact summary for the parent agent.\n\n"
                "Boundaries:\n"
                "- Do not create or delegate to another sub-agent.\n"
                "- Do not include hidden reasoning. Return findings, evidence, constraints, "
                "and useful next steps.\n\n"
                "Skills:\n"
                "- If the task mentions or appears to match a local skill, use list_skills "
                "and read_skill to load the relevant SKILL.md before acting.\n\n"
                "Files:\n"
                "- For broad file questions, whole-file summaries, cross-section searches, "
                "unknown line ranges, or likely large files, call summarize_large_file first.\n"
                "- Use read_workspace_file only for a narrow known line range or to verify "
                "a specific excerpt.\n"
                "- Never scan a file from beginning to end with repeated read_workspace_file calls.\n\n"
                "Commands:\n"
                "- Use run_command_in_container when a command is needed.\n"
                "- Include useful file names, line ranges, and uncertainties in the final summary."
            )
        ),
        *state["messages"],
    ]

    debug_print("SUBAEGNT INPUT MESSAGES", format_messages(llm_messages))

    response = subagent_llm_with_tools.invoke(llm_messages)

    debug_print("SUBAEGNT OUTPUT MESSAGE", format_message(response))
    
    return {"messages": [response]}


subagent_builder = StateGraph(MessagesState)
subagent_builder.add_node("subagent", _subagent_node)
subagent_builder.add_node("tools", ToolNode(base_tools))
subagent_builder.add_edge(START, "subagent")
subagent_builder.add_conditional_edges(
    "subagent",
    tools_condition,
    {
        "tools": "tools",
        "__end__": END,
    },
)
subagent_builder.add_edge("tools", "subagent")
subagent_app = subagent_builder.compile()


@tool
def delegate_to_subagent(
    task: str,
    context: str = "",
    state: Annotated[dict, InjectedState()] = None,
) -> str:
    """Delegate a bounded research or file-reading task to a non-recursive sub-agent."""
    debug_print("TOOL delegate_to_subagent INPUT", f"task={task!r}, context={context!r}")

    parent_context = _format_parent_context(state)
    prompt = (
        "Delegated task:\n"
        f"{task}\n\n"
        "Extra context from parent agent:\n"
        f"{context or '(none)'}\n\n"
        "Recent parent conversation context:\n"
        f"{parent_context}\n\n"
        "Return only the useful findings, constraints, and next-step recommendations. "
        "Do not include hidden reasoning."
    )

    try:
        result = subagent_app.invoke(
            {"messages": [HumanMessage(content=prompt)]},
            config={"recursion_limit": SUBAGENT_MAX_STEPS},
        )
    except GraphRecursionError:
        output = (
            f"子 Agent 超过最大循环次数：{SUBAGENT_MAX_STEPS}。\n"
            "这通常说明委派任务过宽，或需要读取/检查的内容太多。\n"
            "建议父 Agent 将任务拆小后再次委派，例如限定文件、章节、关键词或行号范围。\n\n"
            f"原始委派任务：{task}"
        )
        debug_print("TOOL delegate_to_subagent OUTPUT", output)
        return output

    final_message = result["messages"][-1]
    output = final_message.content

    if len(output) > SUBAGENT_RESULT_LIMIT:
        output = output[:SUBAGENT_RESULT_LIMIT] + "\n... 子 Agent 结果已截断 ..."

    debug_print("TOOL delegate_to_subagent OUTPUT", output)
    return output
