"""Public API for durable Core telemetry."""

from .bus import EventBus
from .context import (
    bind_context,
    bind_run_context,
    current_context,
    reset_context,
)
from .domain import *
from .factory import create_event_bus
from .models import BatchEventSink, EventSink, TelemetryContext, TelemetryEvent
from .recorder import (
    emit_event,
    event_span,
    flush_events,
    install_event_bus,
    record_error,
)
from .serialization import event_to_dict, sanitize_payload
from .sinks import (
    BaseEventSink,
    BufferedEventSink,
    ConsoleEventSink,
    JsonlFileEventSink,
    NoopEventSink,
    PostgresEventSink,
)
