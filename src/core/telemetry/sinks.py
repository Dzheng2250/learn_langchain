"""Telemetry destinations and reusable buffering infrastructure."""

import json
import os
from src.config.settings import AGENT_EVENTS_FILE_PATH
from src.config.paths import telemetry_dir
from src.core.common.batching import BoundedBatchWorker
from src.core.common.debug import debug_print
from src.core.telemetry.models import BatchEventSink, TelemetryEvent
from src.core.telemetry.serialization import event_to_dict


class BaseEventSink:
    """Default no-op lifecycle methods for unbuffered sinks."""

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class NoopEventSink(BaseEventSink):
    """Discard telemetry intentionally."""

    def emit(self, event: TelemetryEvent) -> None:
        pass


class ConsoleEventSink(BaseEventSink):
    """Print compact sanitized telemetry for local debugging."""

    def emit(self, event: TelemetryEvent) -> None:
        debug_print(
            f"EVENT {event.event_type}",
            json.dumps(event_to_dict(event), ensure_ascii=False, default=str),
        )


class JsonlFileEventSink(BaseEventSink):
    """Append event batches to a local JSONL file."""

    def __init__(self, path: str = AGENT_EVENTS_FILE_PATH) -> None:
        self.path = path or str(telemetry_dir() / "events.jsonl")

    def emit(self, event: TelemetryEvent) -> None:
        self.emit_batch([event])

    def emit_batch(self, events: list[TelemetryEvent]) -> None:
        """Write one batch with one open/close cycle."""
        if not events:
            return
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as file:
            file.writelines(
                json.dumps(event_to_dict(event), ensure_ascii=False, default=str) + "\n"
                for event in events
            )


class PostgresEventSink(BaseEventSink):
    """Synchronously persist event batches through a shared connection pool."""

    def __init__(self, pool) -> None:
        self._pool = pool
        from src.core.database.queries import INSERT_AGENT_EVENT
        from psycopg.types.json import Jsonb

        self._insert_query = INSERT_AGENT_EVENT
        self._Jsonb = Jsonb

    def emit(self, event: TelemetryEvent) -> None:
        self.emit_batch([event])

    def emit_batch(self, events: list[TelemetryEvent]) -> None:
        """Persist one event batch in a single database transaction."""
        if not events:
            return
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    self._insert_query,
                    [self._event_params(event) for event in events],
                )
            conn.commit()

    def _event_params(self, event: TelemetryEvent) -> tuple:
        return (
            event.run_id,
            event.workspace_id,
            event.session_id,
            event.turn_index,
            event.event_type,
            event.source,
            event.level,
            event.message,
            self._Jsonb(event.payload),
            event.duration_ms,
            event.created_at,
        )


class BufferedEventSink:
    """Move writes to one bounded background queue and submit event batches."""

    def __init__(
        self,
        sink: BatchEventSink,
        *,
        batch_size: int,
        flush_interval_seconds: float,
        queue_max_size: int,
    ) -> None:
        self.sink = sink
        self._closed = False
        self._worker = BoundedBatchWorker(
            sink.emit_batch,
            batch_size=batch_size,
            flush_interval_seconds=flush_interval_seconds,
            queue_max_size=queue_max_size,
            name="telemetry-buffer-writer",
            on_error=lambda exc: debug_print("TELEMETRY BATCH WRITE ERROR", str(exc)),
            on_drop=lambda event: debug_print(
                "TELEMETRY QUEUE FULL",
                f"dropped event_type={event.event_type}, level={event.level}",
            ),
        )

    def emit(self, event: TelemetryEvent) -> None:
        if self._closed:
            return
        self._worker.submit(event)

    def flush(self) -> None:
        self._worker.flush()
        self.sink.flush()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._worker.close(timeout_seconds=2)
        self.sink.close()
