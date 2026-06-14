"""Conservative trace payload filtering and bounded summaries."""

from src.config.settings import TRACE_DATA_PREVIEW_LIMIT


SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "password",
    "passwd",
    "auth_token",
    "authorization",
    "secret",
    ".env",
    "content",
    "messages",
    "prompt",
    "response",
    "result",
    "output",
    "input",
    "args",
    "original_input",
    "instruction",
    "message",
}


def sanitize_trace_data(value):
    """Remove content-bearing fields and bound every remaining string."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _sensitive(str(key)) else sanitize_trace_data(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_trace_data(item) for item in value[:50]]
    if isinstance(value, str):
        return _truncate(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _truncate(repr(value))


def safe_field_names(value: dict | None) -> list[str]:
    """Expose only non-sensitive top-level field names."""
    return sorted(str(key) for key in (value or {}) if not _sensitive(str(key)))


def _sensitive(key: str) -> bool:
    lowered = key.lower()
    return lowered in SENSITIVE_KEYS or lowered.endswith(
        ("_api_key", "_password", "_secret", "_auth_token", "_content", "_prompt")
    )


def _truncate(text: str) -> str:
    if len(text) <= TRACE_DATA_PREVIEW_LIMIT:
        return text
    return text[:TRACE_DATA_PREVIEW_LIMIT] + "... trace data truncated ..."
