"""Deterministic policy for deciding when memory extraction is worthwhile."""

from src.config.settings import (
    MEMORY_EXTRACTION_ENABLED,
    MEMORY_EXTRACTION_HINT_KEYWORDS,
    MEMORY_EXTRACTION_INTERVAL_TURNS,
    MEMORY_EXTRACTION_MIN_CHARS,
)


def should_extract_long_term_memory(
    user_input: str,
    turn_index: int,
    turn_messages: list,
) -> bool:
    """Return whether this turn is worth an LLM memory-extraction call."""
    return memory_extraction_reason(user_input, turn_index, turn_messages) not in {
        "disabled",
        "not_triggered",
    }


def memory_extraction_reason(
    user_input: str,
    turn_index: int,
    turn_messages: list,
) -> str:
    """Return why long-term memory extraction should run or be skipped."""
    if not MEMORY_EXTRACTION_ENABLED:
        return "disabled"

    if has_explicit_memory_request(user_input):
        return "explicit_memory_keyword"

    if MEMORY_EXTRACTION_INTERVAL_TURNS > 0 and turn_index % MEMORY_EXTRACTION_INTERVAL_TURNS == 0:
        return "interval_turn"

    if turn_message_chars(turn_messages) >= MEMORY_EXTRACTION_MIN_CHARS:
        return "content_size"

    return "not_triggered"


def has_explicit_memory_request(user_input: str) -> bool:
    """Return whether the user explicitly asked the agent to remember something."""
    lowered_input = user_input.lower()
    return any(keyword.lower() in lowered_input for keyword in MEMORY_EXTRACTION_HINT_KEYWORDS)


def turn_message_chars(turn_messages: list) -> int:
    """Return the approximate character count of one completed turn."""
    total_chars = 0
    for message in turn_messages:
        content = getattr(message, "content", "")
        total_chars += len(content) if isinstance(content, str) else len(repr(content))
    return total_chars
