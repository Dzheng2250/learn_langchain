import os
from dotenv import load_dotenv
from langgraph.errors import GraphRecursionError
from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from agent_config import FILE_READ_CHUNK_LINES, MAX_GRAPH_STEPS, MODEL
from agent_debug import debug_print, format_message, format_messages
from agent_subagent import delegate_to_subagent
from agent_tools import parent_base_tools, skill_store

load_dotenv()
API_KEY = os.getenv('ALIYUN_API_KEY')
BASE_URL = os.getenv('ALIYUN_BASE_URL')

# 初始化大模型客户端；streaming=True 表示允许流式返回模型生成内容。
llm = ChatOpenAI(
    model=MODEL,
    api_key=API_KEY,
    base_url=BASE_URL,
    temperature=0.7,
    streaming=True,
)


# 工具列表会同时交给模型和 ToolNode：
# - 模型根据工具描述决定是否发起工具调用。
# - ToolNode 根据模型生成的 tool_calls 真正执行对应函数。
parent_tools = [*parent_base_tools, delegate_to_subagent]
llm_with_tools = llm.bind_tools(parent_tools)


def _format_skill_manifest_context() -> str:
    """Load local skill manifests for the parent agent system prompt."""
    return skill_store.format_skill_list()


def agent_node(state: MessagesState) -> dict:
    """Agent 节点：读取消息历史，调用绑定工具后的模型。"""
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


# 创建一个以 messages 为状态的 LangGraph。
builder = StateGraph(MessagesState)

# agent 节点负责让模型思考和生成回复；tools 节点负责执行工具。
builder.add_node("agent", agent_node)
builder.add_node("tools", ToolNode(parent_tools))

# 每一轮图执行都先进入 agent 节点。
builder.add_edge(START, "agent")

# agent 执行后，如果最后一条 AIMessage 里有 tool_calls，就进入 tools；
# 如果没有工具调用，就结束本轮流程。
builder.add_conditional_edges(
    "agent",
    tools_condition,
    {
        "tools": "tools",
        "__end__": END,
    },
)

# 工具执行完成后要回到 agent，让模型基于工具结果组织最终回答。
builder.add_edge("tools", "agent")

# 编译后得到真正可运行的图应用。
app = builder.compile()


def run_agent_loop() -> None:
    """运行一个最小命令行 Agent 循环。"""
    # messages 用来在多轮对话之间保存历史消息。
    messages = []

    print("Agent started. Type 'exit' or 'quit' to stop.")

    while True:
        user_input = input("\nYou: ").strip()
        if user_input.lower() in {"exit", "quit"}:
            print("Bye.")
            break

        # 本轮输入 = 历史消息 + 当前用户消息。
        inputs = {
            "messages": [*messages, HumanMessage(content=user_input)]
        }

        final_state = None
        print("AI: ", end="", flush=True)

        # 同时监听两类流式事件：
        # - messages：模型生成过程中的 token/chunk，用于实时打印。
        # - values：图状态更新，用于在结束后保存完整消息历史。
        try:
            for stream_mode, chunk in app.stream(
                inputs,
                config={"recursion_limit": MAX_GRAPH_STEPS},
                stream_mode=["messages", "values"],
            ):
                if stream_mode == "messages":
                    message_chunk, _metadata = chunk
                    if message_chunk.content:
                        print(message_chunk.content, end="", flush=True)
                elif stream_mode == "values":
                    final_state = chunk
        except GraphRecursionError:
            print(f"\n本轮对话超过最大循环次数：{MAX_GRAPH_STEPS}")

        print()

        # 保存本轮执行后的完整消息历史，供下一轮继续对话。
        if final_state is not None:
            messages = final_state["messages"]


if __name__ == "__main__":
    run_agent_loop()
