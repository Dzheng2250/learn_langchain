"""Optional bounded debug rendering for model messages and internal values."""

from src.config.settings import DEBUG_AGENT
from src.core.common.content import message_content_text


def is_debug_enabled() -> bool:
    """Return whether agent debug printing is enabled."""
    return DEBUG_AGENT


def debug_print(title: str, value) -> None:
    """Print Agent debug information when DEBUG_AGENT is enabled."""
    if not is_debug_enabled():
        return
    print(f"\n\n========== {title} ==========")
    print(value)
    print("=" * (22 + len(title)))


def format_message(message) -> str:
    """Format a LangChain message into bounded human-readable debug text."""
    lines = [
        f"type: {message.__class__.__name__}",
        f"content: {message_content_text(message)!r}",
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
    """Format a complete message list for debug output."""
    return "\n\n".join(
        f"[{index}]\n{format_message(message)}"
        for index, message in enumerate(messages, start=1)
    )
