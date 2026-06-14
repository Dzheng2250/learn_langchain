"""Telemetry destinations and reusable buffering infrastructure."""

import json
import os
import queue
import threading
import time

from src.config.settings import AGENT_EVENTS_FILE_PATH
from src.config.paths import telemetry_dir
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
        self.batch_size = max(1, int(batch_size))
        self.flush_interval_seconds = max(0.05, float(flush_interval_seconds))
        self._queue: queue.Queue[TelemetryEvent | None] = queue.Queue(
            maxsize=max(1, int(queue_max_size))
        )
        self._closed = False
        self._worker = threading.Thread(
            target=self._run_writer,
            name="telemetry-buffer-writer",
            daemon=True,
        )
        self._worker.start()

    def emit(self, event: TelemetryEvent) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            debug_print(
                "TELEMETRY QUEUE FULL",
                f"dropped event_type={event.event_type}, level={event.level}",
            )

    def flush(self) -> None:
        self._queue.join()
        self.sink.flush()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._queue.join()
        if self._worker.is_alive():
            self._worker.join(timeout=2)
        self.sink.close()

    def _run_writer(self) -> None:
        while True:
            event = self._queue.get()
            if event is None:
                self._queue.task_done()
                return

            batch = [event]
            stop_after_batch = False
            deadline = time.monotonic() + self.flush_interval_seconds
            while len(batch) < self.batch_size:
                timeout = max(0, deadline - time.monotonic())
                if timeout == 0:
                    break
                try:
                    next_event = self._queue.get(timeout=timeout)
                except queue.Empty:
                    break
                if next_event is None:
                    self._queue.task_done()
                    stop_after_batch = True
                    break
                batch.append(next_event)

            try:
                self.sink.emit_batch(batch)
            except Exception as exc:
                debug_print("TELEMETRY BATCH WRITE ERROR", str(exc))
            finally:
                for _item in batch:
                    self._queue.task_done()

            if stop_after_batch:
                return
