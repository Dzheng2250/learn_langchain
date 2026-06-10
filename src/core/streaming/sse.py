import json

from src.core.streaming.events import stream_agent_events


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
