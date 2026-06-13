"""Sanitize observation payloads before they leave the business boundary."""

from dataclasses import asdict

from src.config.settings import AGENT_EVENTS_PAYLOAD_PREVIEW_LIMIT
from src.core.hooks.models import AgentEvent


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

def event_to_dict(event: AgentEvent) -> dict:
    """Convert an event to a JSON-serializable dictionary."""
    data = asdict(event)
    data["created_at"] = event.created_at.isoformat()
    data["workspace_id"] = str(event.workspace_id) if event.workspace_id else None
    data["session_id"] = str(event.session_id) if event.session_id else None
    return data


def sanitize_payload(value):
    """Redact sensitive keys and truncate long payload values."""
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                sanitized[key_text] = "[REDACTED]"
            else:
                sanitized[key_text] = sanitize_payload(item)
        return sanitized

    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]

    if isinstance(value, tuple):
        return [sanitize_payload(item) for item in value]

    if isinstance(value, str):
        return _truncate_text(value)

    if isinstance(value, (int, float, bool)) or value is None:
        return value

    return _truncate_text(repr(value))

def _is_sensitive_key(key: str) -> bool:
    """Return whether a payload key may contain credentials or secrets."""
    lowered = key.lower()
    return any(term in lowered for term in SENSITIVE_KEYS)


def _truncate_text(text: str) -> str:
    """Bound payload text to the configured observation preview limit."""
    if len(text) <= AGENT_EVENTS_PAYLOAD_PREVIEW_LIMIT:
        return text
    return text[:AGENT_EVENTS_PAYLOAD_PREVIEW_LIMIT] + "\n... event payload truncated ..."
