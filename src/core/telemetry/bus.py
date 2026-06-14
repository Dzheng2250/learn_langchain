"""Failure-isolated fan-out for telemetry events."""

from src.core.common.debug import debug_print
from src.core.telemetry.models import EventSink, TelemetryEvent


class EventBus:
    """Broadcast events to fixed sinks owned by the Core process lifecycle."""

    def __init__(self, sinks: list[EventSink]) -> None:
        self.sinks = list(sinks)

    def publish(self, event: TelemetryEvent) -> None:
        """Deliver one event to every sink without affecting Agent behavior."""
        for sink in self.sinks:
            try:
                sink.emit(event)
            except Exception as exc:
                debug_print("TELEMETRY SINK ERROR", f"{sink.__class__.__name__}: {exc}")

    def flush(self) -> None:
        """Wait for every sink to finish pending writes."""
        for sink in self.sinks:
            try:
                sink.flush()
            except Exception as exc:
                debug_print("TELEMETRY SINK FLUSH ERROR", f"{sink.__class__.__name__}: {exc}")

    def close(self) -> None:
        """Close every sink without allowing one failure to block another."""
        for sink in reversed(self.sinks):
            try:
                sink.close()
            except Exception as exc:
                debug_print("TELEMETRY SINK CLOSE ERROR", f"{sink.__class__.__name__}: {exc}")
