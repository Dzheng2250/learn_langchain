"""Shared redaction helpers for logs, telemetry, approvals, and frontends."""

import re
from typing import Any


REDACTED = "[REDACTED]"
MAX_DEPTH = 20
SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "passwd",
    "password",
    "secret",
    "token",
)
_TEXT_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)((?:api[_-]?key|token|password|secret)\s*=\s*)(\S+)"),
    re.compile(r"(?i)(--(?:api[_-]?key|token|password|secret)\s+)(\S+)"),
)


def is_sensitive_key(key: str) -> bool:
    """Return whether a field name indicates secret-bearing content."""
    normalized = key.casefold()
    return any(part in normalized for part in SENSITIVE_KEY_PARTS) or ".env" in normalized


def redact_text(value: str, *, limit: int | None = None) -> str:
    """Redact embedded credentials before applying an optional preview limit."""
    for pattern in _TEXT_PATTERNS:
        value = pattern.sub(rf"\1{REDACTED}", value)
    if limit is not None and len(value) > limit:
        return value[:limit] + "\n... truncated ..."
    return value


def sanitize_value(
    value: Any,
    *,
    key: str = "",
    text_limit: int | None = None,
    list_limit: int | None = None,
    depth: int = 0,
) -> Any:
    """Recursively redact sensitive fields with bounded traversal and previews."""
    if depth > MAX_DEPTH:
        return "[MAX_DEPTH]"
    if is_sensitive_key(key):
        return REDACTED
    if isinstance(value, dict):
        return {
            str(child_key): sanitize_value(
                child_value,
                key=str(child_key),
                text_limit=text_limit,
                list_limit=list_limit,
                depth=depth + 1,
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        items = value if list_limit is None else value[:list_limit]
        result = [
            sanitize_value(
                item,
                key=key,
                text_limit=text_limit,
                list_limit=list_limit,
                depth=depth + 1,
            )
            for item in items
        ]
        if list_limit is not None and len(value) > list_limit:
            result.append("... truncated ...")
        return result
    if isinstance(value, str):
        return redact_text(value, limit=text_limit)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_text(repr(value), limit=text_limit)
