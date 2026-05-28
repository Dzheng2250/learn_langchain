from agent_config import DEBUG_AGENT


def is_debug_enabled() -> bool:
    """Return whether agent debug printing is enabled."""
    return DEBUG_AGENT


def debug_print(title: str, value) -> None:
    """打印 Agent 调试信息；设置 DEBUG_AGENT=0 可以关闭。"""
    if not is_debug_enabled():
        return
    print(f"\n\n========== {title} ==========")
    print(value)
    print("=" * (22 + len(title)))


def format_message(message) -> str:
    """把 LangChain 消息对象格式化成便于观察的文本。"""
    lines = [
        f"type: {message.__class__.__name__}",
        f"content: {repr(message.content)}",
    ]

    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        lines.append(f"tool_calls: {tool_calls!r}")

    invalid_tool_calls = getattr(message, "invalid_tool_calls", None)
    if invalid_tool_calls:
        lines.append(f"invalid_tool_calls: {invalid_tool_calls!r}")

    tool_call_id = getattr(message, "tool_call_id", None)
    if tool_call_id:
        lines.append(f"tool_call_id: {tool_call_id}")

    additional_kwargs = getattr(message, "additional_kwargs", None)
    if additional_kwargs:
        lines.append(f"additional_kwargs: {additional_kwargs!r}")

    response_metadata = getattr(message, "response_metadata", None)
    if response_metadata:
        lines.append(f"response_metadata: {response_metadata!r}")

    return "\n".join(lines)


def format_messages(messages) -> str:
    """格式化完整消息列表。"""
    return "\n\n".join(
        f"[{index}]\n{format_message(message)}"
        for index, message in enumerate(messages, start=1)
    )
