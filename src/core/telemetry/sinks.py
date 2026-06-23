"""Telemetry destinations and reusable buffering infrastructure."""

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config.settings import AGENT_EVENTS_FILE_PATH
from src.config.paths import telemetry_db, telemetry_dir
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


class SQLiteEventSink(BaseEventSink):
    """Persist queryable telemetry in a dedicated local SQLite database.

    The database is deliberately separate from authoritative Session state so
    best-effort observation writes cannot contend with Turn commits.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        retention_days: int = 30,
        busy_timeout_ms: int = 2000,
    ) -> None:
        if retention_days <= 0:
            raise ValueError("Telemetry retention_days must be greater than zero")
        self.path = Path(path).expanduser().resolve() if path else telemetry_db()
        self.retention_days = int(retention_days)
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))
        self._initialize()

    def emit(self, event: TelemetryEvent) -> None:
        self.emit_batch([event])

    def emit_batch(self, events: list[TelemetryEvent]) -> None:
        """Commit one sanitized event batch in one short transaction."""
        if not events:
            return
        with closing(self._connect()) as conn:
            conn.executemany(
                """
                INSERT INTO telemetry_events(
                    run_id, workspace_id, session_id, turn_index, event_type,
                    source, level, message, payload, duration_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [self._event_params(event) for event in events],
            )
            conn.commit()

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS telemetry_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL DEFAULT '',
                    workspace_id TEXT,
                    session_id TEXT,
                    turn_index INTEGER,
                    event_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL DEFAULT '{}',
                    duration_ms INTEGER,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_telemetry_events_session
                ON telemetry_events(workspace_id, session_id, created_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_telemetry_events_run
                ON telemetry_events(run_id, event_id);
                CREATE INDEX IF NOT EXISTS idx_telemetry_events_type
                ON telemetry_events(event_type, created_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_telemetry_events_created
                ON telemetry_events(created_at, event_id);
                """
            )
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
            conn.execute(
                "DELETE FROM telemetry_events WHERE created_at < ?",
                (cutoff.isoformat(),),
            )
            conn.commit()

    def _connect(self):
        conn = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
        )
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        return conn

    @staticmethod
    def _event_params(event: TelemetryEvent) -> tuple:
        return (
            event.run_id,
            str(event.workspace_id) if event.workspace_id is not None else None,
            str(event.session_id) if event.session_id is not None else None,
            event.turn_index,
            event.event_type,
            event.source,
            event.level,
            event.message,
            json.dumps(event.payload, ensure_ascii=False, default=str),
            event.duration_ms,
            event.created_at.isoformat(),
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
