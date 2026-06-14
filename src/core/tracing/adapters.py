"""Adapters from existing observations into the system Trace timeline."""

from src.core.tracing.models import TraceDirection, TraceLayer
from src.core.tracing.recorder import record_trace


TELEMETRY_FIELDS = {
    "tool",
    "tool_call_id",
    "purpose",
    "stop_reason",
    "error_type",
    "error_category",
    "provider",
    "provider_code",
    "http_status",
    "returncode",
    "content_chars",
    "output_chars",
    "tool_call_count",
    "slice_count",
    "graph_steps_used",
}


class TelemetryTraceSink:
    """Translate safe telemetry summaries into the cross-layer trace."""

    def emit(self, event) -> None:
        layer = TraceLayer.TOOL if event.event_type.startswith(("tool_", "command_")) else TraceLayer.TELEMETRY
        payload = {key: value for key, value in event.payload.items() if key in TELEMETRY_FIELDS}
        record_trace(
            TraceDirection.INTERNAL,
            layer,
            f"telemetry.{event.event_type}",
            run_id=event.run_id or None,
            duration_ms=event.duration_ms,
            data={
                "source": event.source,
                "level": event.level,
                **payload,
            },
        )

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass
