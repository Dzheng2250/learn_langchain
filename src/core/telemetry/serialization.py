"""Sanitize telemetry before it leaves the business boundary."""

from dataclasses import asdict

from src.config.settings import AGENT_EVENTS_PAYLOAD_PREVIEW_LIMIT
from src.core.common.redaction import sanitize_value
from src.core.telemetry.models import TelemetryEvent



def event_to_dict(event: TelemetryEvent) -> dict:
    """Convert an event to a JSON-serializable dictionary."""
    data = asdict(event)
    data["created_at"] = event.created_at.isoformat()
    data["workspace_id"] = str(event.workspace_id) if event.workspace_id else None
    data["session_id"] = str(event.session_id) if event.session_id else None
    return data


def sanitize_payload(value):
    """Redact sensitive keys and truncate long payload values recursively."""
    result = sanitize_value(value, text_limit=AGENT_EVENTS_PAYLOAD_PREVIEW_LIMIT)
    return _telemetry_truncation_marker(result)


def _telemetry_truncation_marker(value):
    """Preserve the established telemetry marker for compatibility."""
    if isinstance(value, dict):
        return {key: _telemetry_truncation_marker(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_telemetry_truncation_marker(item) for item in value]
    marker = "\n... truncated ..."
    if isinstance(value, str) and value.endswith(marker):
        return value.removesuffix(marker) + "\n... event payload truncated ..."
    return value
