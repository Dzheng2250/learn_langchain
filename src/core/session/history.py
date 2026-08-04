"""Frontend-neutral Session history query and sanitization service."""

from __future__ import annotations

import json
from copy import deepcopy
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import messages_from_dict

from src.config.settings import REASONING_DISPLAY, REASONING_PREVIEW_LIMIT
from src.core.common.content import message_content_text, reasoning_content_text
from src.core.common.redaction import is_sensitive_key, redact_text
from src.core.ports.session import SessionLifecycleStore
from src.core.ports.state import ConversationHistoryReader


_CONTENT_KEYS = {"content", "file_content", "old_text", "new_text"}
_TOOL_RESULT_LIMIT = 600
_VERBOSE_TOOL_RESULT_LIMIT = 8000
_HISTORY_PAGE_MAX_BYTES = 786_432
_HISTORY_TRUNCATION_MARKER = "\n\n<History content truncated>"
_VERBOSE_RESULT_TOOLS = {
    "delegate_to_subagent", "task_get", "task_list", "task_plan", "task_update",
}
_CONTENT_RESULT_TOOLS = {
    "read_entire_file",
    "read_workspace_file",
    "read_workspace_file_lite",
}


class SessionHistoryQueryService:
    """Resolve a Session and return safe, versioned conversation pages."""

    def __init__(
        self,
        *,
        lifecycle_store: SessionLifecycleStore,
        history_reader: ConversationHistoryReader,
        reasoning_display: str = REASONING_DISPLAY,
        reasoning_preview_limit: int = REASONING_PREVIEW_LIMIT,
    ) -> None:
        self.lifecycle_store = lifecycle_store
        self.history_reader = history_reader
        self.reasoning_display = _supported_reasoning_display(reasoning_display)
        self.reasoning_preview_limit = max(0, int(reasoning_preview_limit))

    def list_history(
        self,
        workspace_root: str,
        session_name: str,
        *,
        before_turn: int | None = None,
        limit_turns: int = 30,
    ) -> dict[str, Any]:
        """Return committed history without creating a missing Session."""
        workspace = self.lifecycle_store.resolve_workspace(workspace_root)
        existing = self.lifecycle_store.find_session(workspace, session_name)
        if existing is None:
            return self._response(session_name, archived=False)
        session, archived = existing
        page = self.history_reader.list_page(
            session,
            before_turn=before_turn,
            limit_turns=limit_turns,
        )
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for record in page.messages:
            message = self._message_dto(record)
            if message["role"] != "system":
                grouped[record.turn_index].append(message)
        turns = [
            {"turn_index": turn_index, "messages": grouped[turn_index]}
            for turn_index in sorted(grouped)
            if grouped[turn_index]
        ]
        turns, next_before_turn, has_more = _bounded_turn_page(
            turns,
            next_before_turn=page.next_before_turn,
            has_more=page.has_more,
        )
        return self._response(
            session_name,
            archived=archived,
            turns=turns,
            next_before_turn=next_before_turn,
            has_more=has_more,
        )

    @staticmethod
    def _response(
        session_name: str,
        *,
        archived: bool,
        turns: list[dict[str, Any]] | None = None,
        next_before_turn: int | None = None,
        has_more: bool = False,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "session_name": session_name,
            "archived": archived,
            "turns": turns or [],
            "next_before_turn": next_before_turn,
            "has_more": has_more,
        }

    def _message_dto(self, record) -> dict[str, Any]:
        role = _normalized_role(record.role, record.message_type)
        message = _decode_message(record.raw)
        content = _decoded_content(record, message)
        blocks = self._content_blocks(role, content)
        if message is not None and role == "assistant":
            blocks.extend(_missing_tool_call_blocks(message, blocks))
        if role == "tool":
            blocks = [self._tool_result_block(record, message)]
        return {
            "message_id": record.message_id,
            "role": role,
            "message_type": record.message_type,
            "blocks": blocks,
        }

    def _content_blocks(self, role: str, content) -> list[dict[str, Any]]:
        if not isinstance(content, list):
            text = message_content_text(content)
            return [{"type": "text", "text": text}] if text else []
        blocks: list[dict[str, Any]] = []
        for value in content:
            if isinstance(value, str):
                if value:
                    blocks.append({"type": "text", "text": value})
                continue
            if not isinstance(value, Mapping):
                continue
            block_type = str(value.get("type") or "text").casefold()
            if block_type == "text":
                text = value.get("text")
                if text:
                    blocks.append({"type": "text", "text": str(text)})
            elif block_type in {"thinking", "reasoning", "thinking_delta"}:
                if self.reasoning_display == "hidden":
                    continue
                text, _redacted, char_count = reasoning_content_text(
                    value,
                    preview_limit=self.reasoning_preview_limit,
                )
                block = {
                    "type": "reasoning",
                    "char_count": char_count,
                    "redacted": False,
                    "display": self.reasoning_display,
                }
                if self.reasoning_display in {"collapsed", "expanded"} and text:
                    block["content"] = text
                blocks.append(block)
            elif block_type == "redacted_thinking":
                if self.reasoning_display != "hidden":
                    blocks.append({
                        "type": "reasoning",
                        "char_count": 0,
                        "redacted": True,
                        "display": self.reasoning_display,
                    })
            elif block_type in {"tool_use", "tool_call"}:
                args = value.get("input", value.get("args", {}))
                blocks.append({
                    "type": "tool_call",
                    "id": str(value.get("id") or ""),
                    "name": str(value.get("name") or "unknown"),
                    "args": _safe_tool_args(args),
                })
            elif block_type == "tool_result":
                blocks.append(_tool_result_from_block(value))
            elif role != "assistant":
                text = message_content_text(value)
                if text:
                    blocks.append({"type": "text", "text": text})
        return blocks

    @staticmethod
    def _tool_result_block(record, message) -> dict[str, Any]:
        """Project a LangChain ToolMessage whose error state lives in status."""
        name = str(getattr(message, "name", "") or "")
        tool_call_id = str(getattr(message, "tool_call_id", "") or "")
        text = message_content_text(message) if message is not None else record.content
        if message is None:
            text = f"<{len(text)} chars of legacy tool result omitted>"
        if name in _CONTENT_RESULT_TOOLS:
            text = f"<{len(text)} chars of file content omitted>"
        limit = (
            _VERBOSE_TOOL_RESULT_LIMIT
            if name in _VERBOSE_RESULT_TOOLS
            else _TOOL_RESULT_LIMIT
        )
        text = redact_text(text, limit=limit)
        status = str(getattr(message, "status", "") or "")
        return {
            "type": "tool_result",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": text,
            "is_error": status == "error",
        }


def _supported_reasoning_display(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"hidden", "metadata", "collapsed", "expanded"} else "metadata"


def _bounded_turn_page(
    turns: list[dict[str, Any]],
    *,
    next_before_turn: int | None,
    has_more: bool,
) -> tuple[list[dict[str, Any]], int | None, bool]:
    """Keep newest complete Turns inside a conservative NDJSON response budget."""
    selected: list[dict[str, Any]] = []
    used_bytes = 0
    for original_turn in reversed(turns):
        turn = _fit_turn_to_budget(original_turn, _HISTORY_PAGE_MAX_BYTES)
        turn_bytes = _json_bytes(turn)
        if selected and used_bytes + turn_bytes > _HISTORY_PAGE_MAX_BYTES:
            break
        selected.append(turn)
        used_bytes += turn_bytes
    selected.reverse()
    truncated = len(selected) < len(turns)
    effective_has_more = has_more or truncated
    if effective_has_more and selected:
        effective_cursor = int(selected[0]["turn_index"])
    else:
        effective_cursor = next_before_turn if has_more else None
    return selected, effective_cursor, effective_has_more


def _fit_turn_to_budget(
    turn: dict[str, Any],
    max_bytes: int,
) -> dict[str, Any]:
    """Project one oversized Turn without splitting it across history pages."""
    if _json_bytes(turn) <= max_bytes:
        return turn
    upper = max(_projectable_byte_lengths(turn), default=0)
    best = _project_turn(turn, 0)
    if _json_bytes(best) > max_bytes:
        return _minimal_turn_projection(turn)
    lower = 0
    while lower <= upper:
        candidate_limit = (lower + upper) // 2
        candidate = _project_turn(turn, candidate_limit)
        if _json_bytes(candidate) <= max_bytes:
            best = candidate
            lower = candidate_limit + 1
        else:
            upper = candidate_limit - 1
    return best


def _project_turn(turn: dict[str, Any], content_byte_limit: int) -> dict[str, Any]:
    projected = deepcopy(turn)
    projected["truncated"] = True
    for message in projected.get("messages", []):
        if not isinstance(message, dict):
            continue
        for block in message.get("blocks", []):
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            field = {
                "text": "text",
                "reasoning": "content",
                "tool_result": "content",
            }.get(block_type)
            if field:
                _truncate_block_field(block, field, content_byte_limit)
            if block_type == "tool_call" and "args" in block:
                args_bytes = _json_bytes(block["args"])
                if args_bytes > content_byte_limit:
                    block["args"] = {
                        "_truncated": True,
                        "_original_bytes": args_bytes,
                    }
    return projected


def _truncate_block_field(block: dict[str, Any], field: str, max_bytes: int) -> None:
    value = block.get(field)
    if not isinstance(value, str):
        return
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return
    marker = _HISTORY_TRUNCATION_MARKER.encode("utf-8")
    if max_bytes <= len(marker):
        rendered = marker[:max_bytes].decode("utf-8", errors="ignore")
    else:
        prefix = encoded[: max_bytes - len(marker)].decode("utf-8", errors="ignore")
        rendered = prefix + _HISTORY_TRUNCATION_MARKER
    block[field] = rendered
    block["truncated"] = True
    block["char_count"] = len(value)
    block["original_bytes"] = len(encoded)


def _projectable_byte_lengths(turn: dict[str, Any]) -> list[int]:
    lengths: list[int] = []
    for message in turn.get("messages", []):
        if not isinstance(message, dict):
            continue
        for block in message.get("blocks", []):
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            field = {
                "text": "text",
                "reasoning": "content",
                "tool_result": "content",
            }.get(block_type)
            value = block.get(field) if field else None
            if isinstance(value, str):
                lengths.append(len(value.encode("utf-8")))
            if block_type == "tool_call" and "args" in block:
                lengths.append(_json_bytes(block["args"]))
    return lengths


def _minimal_turn_projection(turn: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded diagnostic when even block metadata is pathological."""
    return {
        "turn_index": int(turn.get("turn_index") or 0),
        "truncated": True,
        "original_message_count": len(turn.get("messages", [])),
        "messages": [{
            "message_id": "",
            "role": "assistant",
            "message_type": "HistoryProjection",
            "blocks": [{
                "type": "text",
                "text": "<Turn content omitted because it exceeds the history response limit.>",
                "truncated": True,
            }],
        }],
    }


def _json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _tool_result_from_block(value: Mapping) -> dict[str, Any]:
    """Project a native Anthropic tool_result block with an is_error field."""
    name = str(value.get("name") or "")
    text = message_content_text(value.get("content"))
    if name in _CONTENT_RESULT_TOOLS:
        text = f"<{len(text)} chars of file content omitted>"
    limit = _VERBOSE_TOOL_RESULT_LIMIT if name in _VERBOSE_RESULT_TOOLS else _TOOL_RESULT_LIMIT
    return {
        "type": "tool_result",
        "tool_call_id": str(value.get("tool_use_id") or value.get("tool_call_id") or ""),
        "name": name,
        "content": redact_text(text, limit=limit),
        "is_error": bool(value.get("is_error")),
    }


def _normalized_role(role: str, message_type: str) -> str:
    normalized = str(role or "").casefold()
    if normalized in {"user", "human"}:
        return "user"
    if normalized in {"assistant", "ai"}:
        return "assistant"
    if normalized in {"tool", "system"}:
        return normalized
    return {
        "humanmessage": "user",
        "aimessage": "assistant",
        "toolmessage": "tool",
        "systemmessage": "system",
    }.get(str(message_type or "").casefold(), "unknown")


def _decode_message(raw):
    if not isinstance(raw, dict):
        return None
    try:
        return messages_from_dict([raw])[0]
    except (KeyError, TypeError, ValueError):
        return None


def _decoded_content(record, message):
    if message is not None:
        return getattr(message, "content", record.content)
    if isinstance(record.raw, dict):
        if isinstance(record.raw.get("data"), dict) and "content" in record.raw["data"]:
            return record.raw["data"]["content"]
        if "content" in record.raw:
            return record.raw["content"]
    return record.content


def _missing_tool_call_blocks(message, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing = {block.get("id") for block in blocks if block.get("type") == "tool_call"}
    result = []
    for call in getattr(message, "tool_calls", None) or []:
        call_id = str(call.get("id") or "")
        if call_id in existing:
            continue
        result.append({
            "type": "tool_call",
            "id": call_id,
            "name": str(call.get("name") or "unknown"),
            "args": _safe_tool_args(call.get("args", {})),
        })
    return result


def _safe_tool_args(value, *, key: str = "", depth: int = 0):
    if depth > 3:
        return "<nested value omitted>"
    normalized_key = key.casefold()
    if is_sensitive_key(normalized_key):
        return "<redacted>"
    if isinstance(value, str):
        if normalized_key in _CONTENT_KEYS:
            return f"<{len(value)} chars omitted>"
        return redact_text(value, limit=240)
    if isinstance(value, Mapping):
        return {
            str(item_key): _safe_tool_args(item, key=str(item_key), depth=depth + 1)
            for item_key, item in list(value.items())[:30]
        }
    if isinstance(value, (list, tuple)):
        return [_safe_tool_args(item, depth=depth + 1) for item in value[:20]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:240]
