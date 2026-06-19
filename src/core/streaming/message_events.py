"""Convert completed LangGraph messages into stable step events."""

TOOL_RESULT_PREVIEW_LIMIT = 600
PLANNING_TOOL_PREVIEW_LIMIT = 8000
DELEGATION_RESULT_PREVIEW_LIMIT = 6000
VERBOSE_RESULT_TOOLS = {"task_plan", "task_update", "task_list", "task_get"}


def message_text(message) -> str:
    """Return message content as text for event payloads."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return repr(content)


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


def step_events_from_message(message) -> list[dict]:
    """Convert completed graph messages into step-level events."""
    message_type = message.__class__.__name__

    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        return [
            {
                "event": "step",
                "data": {
                    "type": "tool_call_start",
                    "tool": tool_call.get("name"),
                    "args": tool_call.get("args"),
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

