"""Public telemetry recording API used by Core business modules."""

import time
from contextlib import contextmanager

from src.config.settings import AGENT_EVENTS_ENABLED
from src.core.telemetry.bus import EventBus
from src.core.telemetry.context import current_context
from src.core.telemetry.models import TelemetryEvent
from src.core.telemetry.serialization import sanitize_payload
from src.core.telemetry.sinks import NoopEventSink


_bus: EventBus = EventBus([NoopEventSink()])


def install_event_bus(bus: EventBus | None) -> None:
    """Install the process-owned bus without implicitly creating resources."""
    global _bus
    previous = _bus
    _bus = bus or EventBus([NoopEventSink()])
    if previous is not _bus:
        previous.close()


def emit_event(
    event_type: str,
    source: str,
    message: str = "",
    payload: dict | None = None,
    level: str = "info",
    duration_ms: int | None = None,
) -> TelemetryEvent:
    """Create, sanitize, and publish one observation event."""
    context = current_context()
    event = TelemetryEvent(
        event_type=event_type,
        source=source,
        message=message,
        payload=sanitize_payload(payload or {}),
        level=level,
        workspace_id=context.workspace_id,
        session_id=context.session_id,
        turn_index=context.turn_index,
        run_id=context.run_id,
        duration_ms=duration_ms,
    )
    if AGENT_EVENTS_ENABLED:
        _bus.publish(event)
    return event


def record_error(
    source: str,
    operation: str,
    error,
    message: str = "",
    payload: dict | None = None,
    event_type: str | None = None,
    duration_ms: int | None = None,
) -> TelemetryEvent:
    error_payload = dict(payload or {})
    error_payload.update(
        {
            "operation": operation,
            "error_type": error.__class__.__name__,
            "error": str(error),
        }
    )
    return emit_event(
        event_type or f"{operation}_failed",
        source,
        message or f"{operation} failed.",
        error_payload,
        level="error",
        duration_ms=duration_ms,
    )


@contextmanager
def event_span(
    name: str,
    source: str,
    message: str = "",
    payload: dict | None = None,
    level: str = "info",
):
    """Record started/finished/failed events around one operation."""
    started_at = time.monotonic()
    emit_event(f"{name}_started", source, message or f"{name} started.", payload, level=level)
    try:
        yield
    except Exception as exc:
        record_error(
            source,
            name,
            exc,
            message or f"{name} failed.",
            payload=payload,
            duration_ms=int((time.monotonic() - started_at) * 1000),
        )
        raise
    else:
        emit_event(
            f"{name}_finished",
            source,
            message or f"{name} finished.",
            payload,
            level=level,
            duration_ms=int((time.monotonic() - started_at) * 1000),
        )


def flush_events() -> None:
    """Wait for configured sinks to finish pending writes."""
    _bus.flush()
