"""Convert completed LangGraph messages into stable step events."""

from src.core.common.content import message_content_text
from src.core.tools.workspace_patch import parse_workspace_patch

TOOL_RESULT_PREVIEW_LIMIT = 600
PLANNING_TOOL_PREVIEW_LIMIT = 8000
DELEGATION_RESULT_PREVIEW_LIMIT = 6000
VERBOSE_RESULT_TOOLS = {"task_plan", "task_update", "task_list", "task_get"}


def message_text(message) -> str:
    """Return message content as text for event payloads."""
    return message_content_text(message)


def message_preview(message, limit: int = 600) -> str:
    """Return a short content preview for step events."""
    text = message_text(message)
    if len(text) > limit:
        return text[:limit] + "\n... truncated ..."
    return text


def tool_result_limit(message) -> int:
    """Return a safe preview limit for one tool result event."""
    tool_name = getattr(message, "name", None)
    if tool_name in VERBOSE_RESULT_TOOLS:
        return PLANNING_TOOL_PREVIEW_LIMIT
    if tool_name == "delegate_to_subagent":
        return DELEGATION_RESULT_PREVIEW_LIMIT
    return TOOL_RESULT_PREVIEW_LIMIT


def tool_calls_from_message(message) -> list[dict]:
    """Return normalized tool calls from LangChain fields or Anthropic blocks."""
    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        return list(tool_calls)
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return []
    normalized = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "").casefold()
        if block_type not in {"tool_use", "tool_call"}:
            continue
        args = block.get("input")
        if args is None:
            args = block.get("args")
        if args is None:
            args = {}
        normalized.append(
            {
                "name": block.get("name"),
                "args": args,
                "id": block.get("id"),
            }
        )
    return normalized


def step_events_from_message(message) -> list[dict]:
    """Convert completed graph messages into step-level events."""
    message_type = message.__class__.__name__

    tool_calls = tool_calls_from_message(message)
    if tool_calls:
        return [
            {
                "event": "step",
                "data": {
                    "type": "tool_call_start",
                    "tool": tool_call.get("name"),
                    "args": safe_tool_args(
                        str(tool_call.get("name") or ""), tool_call.get("args")
                    ),
                    "id": tool_call.get("id"),
                },
            }
            for tool_call in tool_calls
        ]

    if message_type == "ToolMessage":
        return [
            {
                "event": "step",
                "data": {
                    "type": "tool_call_result",
                    "tool": getattr(message, "name", None),
                    "tool_call_id": getattr(message, "tool_call_id", None),
                    "content": message_preview(message, tool_result_limit(message)),
                },
            }
        ]

    if message_type == "AIMessage":
        return [
            {
                "event": "step",
                "data": {
                    "type": "agent_message",
                    # Terminal fallback when provider does not stream token chunks.
                    # It must carry the full assistant answer; only tool results
                    # use preview truncation.
                    "content": message_text(message),
                },
            }
        ]

    return []


def safe_tool_args(tool_name: str, args):
    """Remove large mutation bodies before tool calls cross the frontend API."""
    if not isinstance(args, dict):
        return args
    if tool_name != "apply_workspace_patch":
        return args
    patch_text = str(args.get("patch") or "")
    try:
        parsed = parse_workspace_patch(patch_text)
        return {
            "valid": True,
            "paths": list(parsed.paths),
            "file_count": len(parsed.files),
            "hunk_count": parsed.hunk_count,
            "patch_chars": len(patch_text),
        }
    except ValueError:
        return {
            "valid": False,
            "patch_chars": len(patch_text),
            "error": "invalid_patch_syntax",
        }
