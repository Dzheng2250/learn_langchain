import json
import os
import atexit
import queue
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from agent_config import (
    AGENT_EVENTS_ASYNC_WRITE,
    AGENT_EVENTS_BATCH_SIZE,
    AGENT_EVENTS_CONSOLE_ENABLED,
    AGENT_EVENTS_ENABLED,
    AGENT_EVENTS_FILE_ENABLED,
    AGENT_EVENTS_FILE_PATH,
    AGENT_EVENTS_FLUSH_INTERVAL_SECONDS,
    AGENT_EVENTS_PAYLOAD_PREVIEW_LIMIT,
    AGENT_EVENTS_POSTGRES_ENABLED,
    AGENT_EVENTS_QUEUE_MAX_SIZE,
    DEFAULT_SESSION_ID,
    MEMORY_DB_HOST,
    MEMORY_DB_NAME,
    MEMORY_DB_PASSWORD,
    MEMORY_DB_PORT,
    MEMORY_DB_USER,
)
from agent_debug import debug_print
from agent_sql import INSERT_AGENT_EVENT


SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "password",
    "passwd",
    "token",
    "secret",
    "authorization",
    ".env",
}


@dataclass
class AgentEvent:
    """Structured observation emitted by the agent harness."""

    event_type: str
    source: str
    message: str = ""
    payload: dict = field(default_factory=dict)
    level: str = "info"
    session_id: str = DEFAULT_SESSION_ID
    turn_index: int | None = None
    run_id: str = ""
    duration_ms: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AgentEventContext:
    """Context shared by events emitted during one agent turn."""

    session_id: str = DEFAULT_SESSION_ID
    turn_index: int | None = None
    run_id: str = ""


@dataclass(frozen=True)
class HookHelperSpec:
    """Public helper metadata for discoverability and conventions."""

    name: str
    event_types: tuple[str, ...]
    description: str


class EventSink(Protocol):
    """Destination for structured agent events."""

    def emit(self, event: AgentEvent) -> None:
        """Write one event."""


class NoopEventSink:
    """Drop events."""

    def emit(self, event: AgentEvent) -> None:
        return


class ConsoleEventSink:
    """Print compact event lines for local debugging."""

    def emit(self, event: AgentEvent) -> None:
        debug_print(
            f"EVENT {event.event_type}",
            json.dumps(event_to_dict(event), ensure_ascii=False, default=str),
        )


class JsonlFileEventSink:
    """Append events to a JSONL file."""

    def __init__(self, path: str = AGENT_EVENTS_FILE_PATH) -> None:
        self.path = path

    def emit(self, event: AgentEvent) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as file:
            file.write(json.dumps(event_to_dict(event), ensure_ascii=False, default=str) + "\n")


class PostgresEventSink:
    """Persist events in PostgreSQL."""

    def __init__(
        self,
        host: str = MEMORY_DB_HOST,
        port: int = MEMORY_DB_PORT,
        dbname: str = MEMORY_DB_NAME,
        user: str = MEMORY_DB_USER,
        password: str = MEMORY_DB_PASSWORD,
        async_write: bool = AGENT_EVENTS_ASYNC_WRITE,
        batch_size: int = AGENT_EVENTS_BATCH_SIZE,
        flush_interval_seconds: float = AGENT_EVENTS_FLUSH_INTERVAL_SECONDS,
        queue_max_size: int = AGENT_EVENTS_QUEUE_MAX_SIZE,
    ) -> None:
        self.host = host
        self.port = port
        self.dbname = dbname
        self.user = user
        self.password = password
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
        return (
            event.run_id,
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

        return ConnectionPool(
            conninfo=make_conninfo(
                "",
                host=self.host,
                port=self.port,
                dbname=self.dbname,
                user=self.user,
                password=self.password,
            ),
            min_size=1,
            max_size=2,
            open=True,
        )

    def _load_jsonb_adapter(self):
        from psycopg.types.json import Jsonb

        return Jsonb


_event_context: ContextVar[AgentEventContext] = ContextVar(
    "agent_event_context",
    default=AgentEventContext(),
)
_event_sinks: list[EventSink] | None = None


def set_event_context(
    session_id: str = DEFAULT_SESSION_ID,
    turn_index: int | None = None,
    run_id: str = "",
) -> None:
    """Set context used by subsequent emitted events."""
    _event_context.set(
        AgentEventContext(
            session_id=session_id,
            turn_index=turn_index,
            run_id=run_id,
        )
    )


def get_event_context() -> AgentEventContext:
    """Return the current event context."""
    return _event_context.get()


def set_event_sinks(sinks: list[EventSink] | None) -> None:
    """Override sinks, mainly for tests."""
    global _event_sinks
    if _event_sinks:
        for sink in _event_sinks:
            close = getattr(sink, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    debug_print("AGENT EVENT SINK CLOSE ERROR", f"{sink.__class__.__name__}: {exc}")
    _event_sinks = sinks


def emit_event(
    event_type: str,
    source: str,
    message: str = "",
    payload: dict | None = None,
    level: str = "info",
    duration_ms: int | None = None,
) -> AgentEvent:
    """Emit one structured agent event without affecting business flow."""
    context = get_event_context()
    event = AgentEvent(
        event_type=event_type,
        source=source,
        message=message,
        payload=sanitize_payload(payload or {}),
        level=level,
        session_id=context.session_id,
        turn_index=context.turn_index,
        run_id=context.run_id,
        duration_ms=duration_ms,
    )

    if not AGENT_EVENTS_ENABLED:
        return event

    for sink in _get_event_sinks():
        try:
            sink.emit(event)
        except Exception as exc:
            debug_print("AGENT EVENT SINK ERROR", f"{sink.__class__.__name__}: {exc}")

    return event


def record_error(
    source: str,
    operation: str,
    error,
    message: str = "",
    payload: dict | None = None,
    event_type: str | None = None,
    duration_ms: int | None = None,
) -> AgentEvent:
    """Record an operation error with a consistent payload shape."""
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


def record_tool_started(
    source: str,
    tool: str | None,
    tool_call_id: str | None = None,
    args=None,
    message: str = "Tool call requested by LLM.",
) -> AgentEvent:
    """Record a tool-start event with a consistent payload shape."""
    return emit_event(
        "tool_started",
        source,
        message,
        {
            "tool": tool,
            "tool_call_id": tool_call_id,
            "args_preview": repr(args),
        },
    )


def record_tool_finished(
    source: str,
    tool: str | None,
    tool_call_id: str | None = None,
    content: str = "",
    message: str = "Tool call result received.",
    duration_ms: int | None = None,
) -> AgentEvent:
    """Record a tool-finished event with a consistent payload shape."""
    return emit_event(
        "tool_finished",
        source,
        message,
        {
            "tool": tool,
            "tool_call_id": tool_call_id,
            "content_preview": content,
            "content_chars": len(content),
        },
        duration_ms=duration_ms,
    )


def record_tool_failed(
    source: str,
    tool: str | None,
    tool_call_id: str | None = None,
    error=None,
    message: str = "Tool call failed.",
    payload: dict | None = None,
    duration_ms: int | None = None,
) -> AgentEvent:
    """Record a tool-failed event with a consistent payload shape."""
    error_payload = dict(payload or {})
    error_payload.update(
        {
            "tool": tool,
            "tool_call_id": tool_call_id,
        }
    )
    if error is not None:
        return record_error(
            source,
            "tool",
            error,
            message=message,
            payload=error_payload,
            event_type="tool_failed",
            duration_ms=duration_ms,
        )
    return emit_event("tool_failed", source, message, error_payload, level="error", duration_ms=duration_ms)


def record_command_started(
    source: str,
    command: str,
    message: str = "Command requested.",
) -> AgentEvent:
    """Record the start of an internal shell/container command."""
    return emit_event(
        "command_started",
        source,
        message,
        {"command_preview": command},
    )


def record_command_finished(
    source: str,
    returncode: int,
    output: str = "",
    message: str = "Command finished.",
    duration_ms: int | None = None,
) -> AgentEvent:
    """Record successful or completed command execution."""
    return emit_event(
        "command_finished",
        source,
        message,
        {
            "returncode": returncode,
            "output_chars": len(output),
            "output_preview": output,
        },
        duration_ms=duration_ms,
    )


def record_command_failed(
    source: str,
    reason: str,
    command: str | None = None,
    returncode: int | None = None,
    detail: str = "",
    message: str = "Command failed.",
    level: str = "error",
    duration_ms: int | None = None,
) -> AgentEvent:
    """Record rejected, failed, or unavailable command execution."""
    payload = {
        "reason": reason,
        "command_preview": command,
        "returncode": returncode,
        "detail_preview": detail,
    }
    return emit_event(
        "command_failed",
        source,
        message,
        payload,
        level=level,
        duration_ms=duration_ms,
    )


def record_memory_saved(
    source: str,
    memory_id: str,
    action: str,
    kind: str,
    importance: int | None = None,
    content: str = "",
    message: str = "Saved long-term memory.",
) -> AgentEvent:
    """Record a memory-save event with a consistent payload shape."""
    return emit_event(
        "memory_saved",
        source,
        message,
        {
            "memory_id": memory_id,
            "action": action,
            "kind": kind,
            "importance": importance,
            "content_preview": content[:120],
        },
    )


HOOK_HELPERS: dict[str, HookHelperSpec] = {
    "emit_event": HookHelperSpec(
        "emit_event",
        ("custom",),
        "Low-level event API. Use only when no domain helper exists.",
    ),
    "event_span": HookHelperSpec(
        "event_span",
        ("<name>_started", "<name>_finished", "<name>_failed"),
        "Context manager for simple operation spans.",
    ),
    "record_error": HookHelperSpec(
        "record_error",
        ("<operation>_failed",),
        "Standard error helper with operation and exception fields.",
    ),
    "record_tool_started": HookHelperSpec(
        "record_tool_started",
        ("tool_started",),
        "Tool boundary start event, emitted by the observed ToolNode wrapper.",
    ),
    "record_tool_finished": HookHelperSpec(
        "record_tool_finished",
        ("tool_finished",),
        "Tool boundary completion event, emitted by the observed ToolNode wrapper.",
    ),
    "record_tool_failed": HookHelperSpec(
        "record_tool_failed",
        ("tool_failed",),
        "Tool boundary failure event, emitted by the observed ToolNode wrapper.",
    ),
    "record_command_started": HookHelperSpec(
        "record_command_started",
        ("command_started",),
        "Internal command start event for command-running tools.",
    ),
    "record_command_finished": HookHelperSpec(
        "record_command_finished",
        ("command_finished",),
        "Internal command completion event for command-running tools.",
    ),
    "record_command_failed": HookHelperSpec(
        "record_command_failed",
        ("command_failed",),
        "Internal command rejection or failure event for command-running tools.",
    ),
    "record_memory_saved": HookHelperSpec(
        "record_memory_saved",
        ("memory_saved",),
        "Long-term memory persistence event.",
    ),
}


def list_hook_helpers() -> dict[str, HookHelperSpec]:
    """Return registered hook helpers and the event types they own."""
    return dict(HOOK_HELPERS)


@contextmanager
def event_span(
    name: str,
    source: str,
    message: str = "",
    payload: dict | None = None,
    level: str = "info",
):
    """Emit started/finished/failed events around an operation."""
    started_at = time.monotonic()
    emit_event(
        f"{name}_started",
        source,
        message or f"{name} started.",
        payload,
        level=level,
    )
    try:
        yield
    except Exception as exc:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        failed_payload = dict(payload or {})
        failed_payload["error"] = str(exc)
        emit_event(
            f"{name}_failed",
            source,
            message or f"{name} failed.",
            failed_payload,
            level="error",
            duration_ms=duration_ms,
        )
        raise
    else:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        emit_event(
            f"{name}_finished",
            source,
            message or f"{name} finished.",
            payload,
            level=level,
            duration_ms=duration_ms,
        )


def flush_event_sinks() -> None:
    """Flush sinks that support flushing."""
    for sink in _get_event_sinks():
        flush = getattr(sink, "flush", None)
        if callable(flush):
            try:
                flush()
            except Exception as exc:
                debug_print("AGENT EVENT SINK FLUSH ERROR", f"{sink.__class__.__name__}: {exc}")


def event_to_dict(event: AgentEvent) -> dict:
    """Convert an event to a JSON-serializable dictionary."""
    data = asdict(event)
    data["created_at"] = event.created_at.isoformat()
    return data


def sanitize_payload(value):
    """Redact sensitive keys and truncate long payload values."""
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                sanitized[key_text] = "[REDACTED]"
            else:
                sanitized[key_text] = sanitize_payload(item)
        return sanitized

    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]

    if isinstance(value, tuple):
        return [sanitize_payload(item) for item in value]

    if isinstance(value, str):
        return _truncate_text(value)

    if isinstance(value, (int, float, bool)) or value is None:
        return value

    return _truncate_text(repr(value))


def _get_event_sinks() -> list[EventSink]:
    global _event_sinks
    if _event_sinks is not None:
        return _event_sinks

    sinks: list[EventSink] = []
    if AGENT_EVENTS_CONSOLE_ENABLED:
        sinks.append(ConsoleEventSink())
    if AGENT_EVENTS_FILE_ENABLED:
        sinks.append(JsonlFileEventSink())
    if AGENT_EVENTS_POSTGRES_ENABLED:
        sinks.append(PostgresEventSink())
    if not sinks:
        sinks.append(NoopEventSink())

    _event_sinks = sinks
    return sinks


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(term in lowered for term in SENSITIVE_KEYS)


def _truncate_text(text: str) -> str:
    if len(text) <= AGENT_EVENTS_PAYLOAD_PREVIEW_LIMIT:
        return text
    return text[:AGENT_EVENTS_PAYLOAD_PREVIEW_LIMIT] + "\n... event payload truncated ..."
