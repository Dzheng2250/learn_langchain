"""Compose the process-level telemetry bus from validated settings."""

from src.config.settings import (
    AGENT_EVENTS_ASYNC_WRITE,
    AGENT_EVENTS_BATCH_SIZE,
    AGENT_EVENTS_CONSOLE_ENABLED,
    AGENT_EVENTS_ENABLED,
    AGENT_EVENTS_FILE_ENABLED,
    AGENT_EVENTS_FLUSH_INTERVAL_SECONDS,
    AGENT_EVENTS_POSTGRES_ENABLED,
    AGENT_EVENTS_QUEUE_MAX_SIZE,
    AGENT_EVENTS_SQLITE_ENABLED,
    AGENT_EVENTS_SQLITE_PATH,
    AGENT_EVENTS_SQLITE_RETENTION_DAYS,
)
from src.core.telemetry.bus import EventBus
from src.core.common.debug import debug_print
from src.core.telemetry.sinks import (
    BufferedEventSink,
    ConsoleEventSink,
    JsonlFileEventSink,
    NoopEventSink,
    PostgresEventSink,
    SQLiteEventSink,
)


def create_event_bus(
    pool=None,
    *,
    include_trace_sink: bool = False,
    include_local_sinks: bool = True,
) -> EventBus:
    """Build the fixed telemetry sink graph owned by one Core process.

    Local sinks are disabled by tests and embedded Core instances that opt out
    of runtime-file ownership. Production daemons leave them enabled.
    """
    sinks = []
    if AGENT_EVENTS_ENABLED and include_trace_sink:
        from src.core.tracing.adapters import TelemetryTraceSink

        sinks.append(TelemetryTraceSink())
    if AGENT_EVENTS_ENABLED and AGENT_EVENTS_CONSOLE_ENABLED:
        sinks.append(ConsoleEventSink())
    if AGENT_EVENTS_ENABLED and include_local_sinks and AGENT_EVENTS_SQLITE_ENABLED:
        try:
            sqlite = SQLiteEventSink(
                AGENT_EVENTS_SQLITE_PATH or None,
                retention_days=AGENT_EVENTS_SQLITE_RETENTION_DAYS,
            )
            sinks.append(_buffered(sqlite) if AGENT_EVENTS_ASYNC_WRITE else sqlite)
        except Exception as exc:
            debug_print("SQLITE TELEMETRY INIT ERROR", str(exc))
    if AGENT_EVENTS_ENABLED and include_local_sinks and AGENT_EVENTS_FILE_ENABLED:
        file_sink = JsonlFileEventSink()
        sinks.append(_buffered(file_sink) if AGENT_EVENTS_ASYNC_WRITE else file_sink)
    if AGENT_EVENTS_ENABLED and AGENT_EVENTS_POSTGRES_ENABLED and pool is not None:
        postgres = PostgresEventSink(pool)
        sinks.append(_buffered(postgres) if AGENT_EVENTS_ASYNC_WRITE else postgres)
    return EventBus(sinks or [NoopEventSink()])


def _buffered(sink) -> BufferedEventSink:
    """Apply the shared bounded background writer to any batch-capable sink."""
    return BufferedEventSink(
        sink,
        batch_size=AGENT_EVENTS_BATCH_SIZE,
        flush_interval_seconds=AGENT_EVENTS_FLUSH_INTERVAL_SECONDS,
        queue_max_size=AGENT_EVENTS_QUEUE_MAX_SIZE,
    )
