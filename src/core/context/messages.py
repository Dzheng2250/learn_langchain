"""Pure message helpers for compact context management."""

from langchain_core.messages import SystemMessage

from src.core.common.debug import format_message


SUMMARY_MESSAGE_PREFIX = "Conversation context summary:"
MEMORY_MESSAGE_PREFIXES = (
    "Relevant long-term memory:",
    "Relevant long-term memory for this workspace:",
)
SUMMARY_SOURCE_MESSAGE_PREVIEW_CHARS = 1200


def is_synthetic_context_message(message) -> bool:
    """Return whether a message was injected for prompt context only."""
    return (
        isinstance(message, SystemMessage)
        and isinstance(message.content, str)
        and (
            message.content.startswith(SUMMARY_MESSAGE_PREFIX)
            or message.content.startswith(MEMORY_MESSAGE_PREFIXES)
        )
    )


def strip_context_messages(messages: list) -> list:
    """Remove synthetic summary/memory messages before durable persistence."""
    return [message for message in messages if not is_synthetic_context_message(message)]


def format_messages_for_summary(
    messages: list,
    *,
    preview_chars: int = SUMMARY_SOURCE_MESSAGE_PREVIEW_CHARS,
) -> str:
    """Format messages for summarization with bounded per-message content."""
    formatted = []
    for index, message in enumerate(messages, start=1):
        text = format_message(message)
        if len(text) > preview_chars:
            text = text[:preview_chars] + "\n... message truncated ..."
        formatted.append(f"[{index}]\n{text}")
    return "\n\n".join(formatted)

