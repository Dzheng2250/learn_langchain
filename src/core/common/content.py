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
