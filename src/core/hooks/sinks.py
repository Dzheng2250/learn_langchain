"""Failure-isolated destinations for structured observation events."""

import atexit
import json
import os
import queue
import threading
import time

from src.core.common.debug import debug_print
from src.config.settings import (
    AGENT_EVENTS_ASYNC_WRITE,
    AGENT_EVENTS_BATCH_SIZE,
    AGENT_EVENTS_FILE_PATH,
    AGENT_EVENTS_FLUSH_INTERVAL_SECONDS,
    AGENT_EVENTS_QUEUE_MAX_SIZE,
    MEMORY_DB_HOST,
    MEMORY_DB_NAME,
    MEMORY_DB_PASSWORD,
    MEMORY_DB_PORT,
    MEMORY_DB_USER,
)
from src.core.database.connection import connection_info
from src.core.database.queries import INSERT_AGENT_EVENT
from src.core.hooks.models import AgentEvent
from src.core.hooks.serialization import event_to_dict


class NoopEventSink:
    """Drop events."""

    def emit(self, event: AgentEvent) -> None:
        """Discard one event intentionally."""
        return


class ConsoleEventSink:
    """Print compact event lines for local debugging."""

    def emit(self, event: AgentEvent) -> None:
        """Print a sanitized event through the debug output helper."""
        debug_print(
            f"EVENT {event.event_type}",
            json.dumps(event_to_dict(event), ensure_ascii=False, default=str),
        )


class JsonlFileEventSink:
    """Append events to a JSONL file."""

    def __init__(self, path: str = AGENT_EVENTS_FILE_PATH) -> None:
        self.path = path

    def emit(self, event: AgentEvent) -> None:
        """Append one JSON-serializable event line to the configured file."""
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as file:
            file.write(json.dumps(event_to_dict(event), ensure_ascii=False, default=str) + "\n")


class PostgresEventSink:
    """Persist events in PostgreSQL."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        dbname: str | None = None,
        user: str | None = None,
        password: str | None = None,
        conninfo: str | None = None,
        async_write: bool = AGENT_EVENTS_ASYNC_WRITE,
        batch_size: int = AGENT_EVENTS_BATCH_SIZE,
        flush_interval_seconds: float = AGENT_EVENTS_FLUSH_INTERVAL_SECONDS,
        queue_max_size: int = AGENT_EVENTS_QUEUE_MAX_SIZE,
    ) -> None:
        """Configure synchronous or queued batch persistence.

        Explicit connection arguments override shared database settings.
        ``async_write`` moves inserts to one background writer thread.
        """
        self.host = host or MEMORY_DB_HOST
        self.port = port or MEMORY_DB_PORT
        self.dbname = dbname or MEMORY_DB_NAME
        self.user = user or MEMORY_DB_USER
        # None means "use the shared default"; an explicit empty string means
        # "connect without a password" and must not silently fall back.
        self.password = password if password is not None else MEMORY_DB_PASSWORD
        self.conninfo = conninfo
        self._use_default_connection = all(
            value is None for value in (host, port, dbname, user, password, conninfo)
        )
        self.async_write = async_write
        self.batch_size = max(1, int(batch_size))
        self.flush_interval_seconds = max(0.05, float(flush_interval_seconds))
        self._pool = self._load_pool()
        self._Jsonb = self._load_jsonb_adapter()
        self._queue: queue.Queue[AgentEvent | None] | None = None
        self._worker: threading.Thread | None = None
        self._closed = False

        if self.async_write:
            self._queue = queue.Queue(maxsize=max(1, int(queue_max_size)))
            self._worker = threading.Thread(
                target=self._run_writer,
                name="agent-event-postgres-writer",
                daemon=True,
            )
            self._worker.start()
            atexit.register(self.close)

    def emit(self, event: AgentEvent) -> None:
        """Queue or synchronously persist one event without blocking producers."""
        if not self.async_write:
            self._write_batch([event])
            return

        if self._closed or self._queue is None:
            return

        try:
            self._queue.put_nowait(event)
        except queue.Full:
            debug_print(
                "AGENT EVENT QUEUE FULL",
                f"dropped event_type={event.event_type}, level={event.level}",
            )

    def flush(self) -> None:
        """Wait until queued events have been written."""
        if self._queue is not None:
            self._queue.join()

    def close(self) -> None:
        """Flush, stop the background writer, and close the connection pool."""
        if self._closed:
            return
        self._closed = True
        if self._queue is not None:
            self._queue.put(None)
            self._queue.join()
            if self._worker is not None and self._worker.is_alive():
                self._worker.join(timeout=2)
        self._pool.close()

    def _run_writer(self) -> None:
        """Drain queued events into bounded batches until the sentinel arrives."""
        assert self._queue is not None
        while True:
            event = self._queue.get()
            if event is None:
                self._queue.task_done()
                return

            batch = [event]
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
                    try:
                        self._write_batch(batch)
                    except Exception as exc:
                        debug_print("AGENT EVENT BATCH WRITE ERROR", str(exc))
                    for _event in batch:
                        self._queue.task_done()
                    return
                batch.append(next_event)

            try:
                self._write_batch(batch)
            except Exception as exc:
                debug_print("AGENT EVENT BATCH WRITE ERROR", str(exc))
            for _event in batch:
                self._queue.task_done()

    def _write_batch(self, events: list[AgentEvent]) -> None:
        """Persist one event batch in a single database transaction."""
        if not events:
            return
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    INSERT_AGENT_EVENT,
                    [self._event_params(event) for event in events],
                )
            conn.commit()

    def _event_params(self, event: AgentEvent) -> tuple:
        """Convert an event into parameters ordered for ``INSERT_AGENT_EVENT``."""
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

    def _connect(self):
        """Return a connection context manager from the pool."""
        return self._pool.connection()

    def _load_pool(self):
        """Lazy-import psycopg_pool and create a ConnectionPool."""
        from psycopg.conninfo import make_conninfo
        from psycopg_pool import ConnectionPool

        conninfo = (
            connection_info()
            if self._use_default_connection
            else self.conninfo
            or make_conninfo(
                "",
                host=self.host,
                port=self.port,
                dbname=self.dbname,
                user=self.user,
                password=self.password,
            )
        )
        return ConnectionPool(
            conninfo=conninfo,
            min_size=1,
            max_size=2,
            open=True,
        )

    def _load_jsonb_adapter(self):
        """Lazy-import and return psycopg's JSONB parameter adapter."""
        from psycopg.types.json import Jsonb

        return Jsonb
