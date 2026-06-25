"""Provider-neutral text extraction for LangChain message content."""

from __future__ import annotations

import json
from collections.abc import Mapping


_NON_TEXT_BLOCK_TYPES = {
    "tool_use",
    "tool_result",
    "server_tool_use",
    "reasoning",
    "thinking",
    "redacted_thinking",
    # Anthropic streams tool arguments and hidden reasoning as delta blocks.
    # They are protocol data, not user-visible assistant text.
    "input_json_delta",
    "thinking_delta",
    "signature_delta",
}

_REASONING_BLOCK_TYPES = {
    "reasoning",
    "thinking",
    "thinking_delta",
}

_REDACTED_REASONING_BLOCK_TYPES = {
    "redacted_thinking",
}


def message_content_text(message_or_content) -> str:
    """Return user-visible text from a LangChain message or raw content value."""
    content = (
        getattr(message_or_content, "content")
        if hasattr(message_or_content, "content")
        and not isinstance(
            message_or_content,
            (str, bytes, bytearray, Mapping, list, tuple),
        )
        else message_or_content
    )
    return content_text(content)


def content_text(content) -> str:
    """Normalize scalar text or provider content blocks into display text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    if isinstance(content, Mapping):
        if _is_ignored_block(content):
            return ""
        return _block_text(content) or _json_text(content)
    if isinstance(content, (list, tuple)):
        text_parts = []
        fallback_blocks = []
        for item in content:
            text = _block_text(item)
            if text:
                text_parts.append(text)
            elif not _is_ignored_block(item):
                fallback_blocks.append(item)
        if text_parts:
            return "".join(text_parts)
        if not fallback_blocks:
            return ""
        fallback = fallback_blocks if len(fallback_blocks) != 1 else fallback_blocks[0]
        return _json_text(fallback)
    return str(content)


def reasoning_content_text(content, *, preview_limit: int | None = None) -> tuple[str, bool, int]:
    """Return provider reasoning/thinking text without mixing it into answers.

    The returned tuple is ``(text, redacted, char_count)``. ``text`` is truncated
    only by ``preview_limit``; ``char_count`` reports the untruncated extracted
    text length where available. Redacted blocks never expose their hidden text.
    """
    content = (
        getattr(content, "content")
        if hasattr(content, "content")
        and not isinstance(content, (str, bytes, bytearray, Mapping, list, tuple))
        else content
    )
    text_parts: list[str] = []
    redacted = False

    def visit(value) -> None:
        nonlocal redacted
        if isinstance(value, Mapping):
            block_type = str(value.get("type") or "").casefold()
            if block_type in _REDACTED_REASONING_BLOCK_TYPES:
                redacted = True
                return
            if block_type in _REASONING_BLOCK_TYPES:
                extracted = _reasoning_block_text(value)
                if extracted:
                    text_parts.append(extracted)
                return
            delta = value.get("delta")
            if isinstance(delta, Mapping):
                visit(delta)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                visit(item)

    visit(content)
    text = "".join(text_parts)
    char_count = len(text)
    if preview_limit is not None and preview_limit >= 0 and len(text) > preview_limit:
        text = text[:preview_limit]
    return text, redacted, char_count


def _block_text(block) -> str:
    if isinstance(block, str):
        return block
    if not isinstance(block, Mapping):
        return ""
    block_type = str(block.get("type") or "").casefold()
    if block_type in _NON_TEXT_BLOCK_TYPES:
        return ""
    text = block.get("text")
    if isinstance(text, str):
        return text
    if block_type == "text" and text is not None:
        return str(text)
    return ""


def _reasoning_block_text(block: Mapping) -> str:
    for key in ("thinking", "reasoning", "text", "content"):
        value = block.get(key)
        if isinstance(value, str):
            return value
    delta = block.get("delta")
    if isinstance(delta, Mapping):
        return _reasoning_block_text(delta)
    if isinstance(delta, str):
        return delta
    return ""


def _is_ignored_block(block) -> bool:
    return (
        isinstance(block, Mapping)
        and str(block.get("type") or "").casefold() in _NON_TEXT_BLOCK_TYPES
    )


def _json_text(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)
