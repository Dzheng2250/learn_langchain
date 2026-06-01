import json

from langchain_core.messages import HumanMessage
from langgraph.errors import GraphRecursionError

from agent_config import MAX_GRAPH_STEPS


def _message_text(message) -> str:
    """Return message content as text for event payloads."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return repr(content)


def _message_preview(message, limit: int = 600) -> str:
    """Return a short content preview for step events."""
    text = _message_text(message)
    if len(text) > limit:
        return text[:limit] + "\n... truncated ..."
    return text


def _step_events_from_message(message) -> list[dict]:
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
                    "content": _message_preview(message),
                },
            }
        ]

    if message_type == "AIMessage":
        return [
            {
                "event": "step",
                "data": {
                    "type": "agent_message",
                    "content": _message_preview(message),
                },
            }
        ]

    return []


def stream_agent_events(app, messages: list, user_input: str):
    """Yield step/token/done/error events for one user turn."""
    inputs = {
        "messages": [*messages, HumanMessage(content=user_input)]
    }
    final_state = None
    seen_message_count = len(inputs["messages"])

    yield {
        "event": "step",
        "data": {
            "type": "agent_start",
            "message": "Agent turn started.",
        },
    }

    try:
        for stream_mode, chunk in app.stream(
            inputs,
            config={"recursion_limit": MAX_GRAPH_STEPS},
            stream_mode=["messages", "values"],
        ):
            if stream_mode == "messages":
                message_chunk, _metadata = chunk
                if message_chunk.content:
                    yield {
                        "event": "token",
                        "data": {
                            "content": message_chunk.content,
                        },
                    }
            elif stream_mode == "values":
                final_state = chunk
                state_messages = chunk.get("messages", [])
                new_messages = state_messages[seen_message_count:]
                seen_message_count = len(state_messages)

                for message in new_messages:
                    yield from _step_events_from_message(message)
    except GraphRecursionError:
        yield {
            "event": "error",
            "data": {
                "type": "recursion_limit",
                "message": f"Graph exceeded recursion_limit={MAX_GRAPH_STEPS}.",
            },
        }
        return

    yield {
        "event": "done",
        "data": {
            "messages": final_state["messages"] if final_state is not None else messages,
        },
    }


def format_sse_event(event: str, data) -> str:
    """Format one event as a Server-Sent Events frame."""
    payload = json.dumps(data, ensure_ascii=False, default=repr)
    return f"event: {event}\ndata: {payload}\n\n"


def stream_agent_sse(app, messages: list, user_input: str):
    """Yield SSE frames for one user turn."""
    for item in stream_agent_events(app, messages, user_input):
        data = item["data"]
        if item["event"] == "done":
            data = {"status": "ok"}
        yield format_sse_event(item["event"], data)
