"""Event publisher boundary independent from concrete sinks."""

from typing import Protocol

from src.core.common.debug import debug_print
from src.core.hooks.models import AgentEvent, EventSink


class EventPublisher(Protocol):
    def publish(self, event: AgentEvent) -> None:
        """Publish one observation event without changing business behavior."""

    def flush(self) -> None:
        """Flush buffered subscribers."""

    def close(self) -> None:
        """Close publisher-owned subscribers."""


class SinkEventPublisher:
    """Fan out events to failure-isolated sink subscribers."""

    def __init__(self, sinks: list[EventSink]) -> None:
        self.sinks = list(sinks)

    def publish(self, event: AgentEvent) -> None:
        for sink in self.sinks:
            try:
                sink.emit(event)
            except Exception as exc:
                debug_print("AGENT EVENT SINK ERROR", f"{sink.__class__.__name__}: {exc}")

    def flush(self) -> None:
        for sink in self.sinks:
            operation = getattr(sink, "flush", None)
            if callable(operation):
                try:
                    operation()
                except Exception as exc:
                    debug_print("AGENT EVENT SINK FLUSH ERROR", f"{sink.__class__.__name__}: {exc}")

    def close(self) -> None:
        for sink in self.sinks:
            operation = getattr(sink, "close", None)
            if callable(operation):
                try:
                    operation()
                except Exception as exc:
                    debug_print("AGENT EVENT SINK CLOSE ERROR", f"{sink.__class__.__name__}: {exc}")
