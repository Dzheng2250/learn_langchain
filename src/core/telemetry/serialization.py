"""Sanitize telemetry before it leaves the business boundary."""

from dataclasses import asdict

from src.config.settings import AGENT_EVENTS_PAYLOAD_PREVIEW_LIMIT
from src.core.telemetry.models import TelemetryEvent


SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "password",
    "passwd",
    "token",
    "secret",
    "authorization",
    ".env",
}


def event_to_dict(event: TelemetryEvent) -> dict:
    """Convert an event to a JSON-serializable dictionary."""
    data = asdict(event)
    data["created_at"] = event.created_at.isoformat()
    data["workspace_id"] = str(event.workspace_id) if event.workspace_id else None
    data["session_id"] = str(event.session_id) if event.session_id else None
    return data


def sanitize_payload(value):
    """Redact sensitive keys and truncate long payload values recursively."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _is_sensitive_key(str(key)) else sanitize_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return _truncate_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _truncate_text(repr(value))


def _is_sensitive_key(key: str) -> bool:
    return any(term in key.lower() for term in SENSITIVE_KEYS)


def _truncate_text(text: str) -> str:
    if len(text) <= AGENT_EVENTS_PAYLOAD_PREVIEW_LIMIT:
        return text
    return text[:AGENT_EVENTS_PAYLOAD_PREVIEW_LIMIT] + "\n... event payload truncated ..."
