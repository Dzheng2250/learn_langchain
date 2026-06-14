"""Process-owned TraceRecorder and public non-blocking record API."""

import itertools
import time
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from src.core.common.debug import debug_print
from src.core.tracing.context import current_trace_context
from src.core.tracing.models import TraceDirection, TraceLayer, TraceRecord
from src.core.tracing.sanitization import sanitize_trace_data


class NoopTraceRecorder:
    def record(self, *args, **kwargs):
        return None

    def flush(self) -> None:
        pass

    def close(self, timeout_seconds: float = 2.0) -> None:
        pass


class TraceRecorder:
    """Assign process ordering and deliver sanitized records to one writer."""

    def __init__(self, writer, *, daemon_id: str | None = None) -> None:
        self.writer = writer
        self.daemon_id = daemon_id or uuid4().hex
        self._sequence = itertools.count(1)
        self._lock = Lock()

    def record(
        self,
        direction: TraceDirection,
        layer: TraceLayer,
        kind: str,
        *,
        data: dict | None = None,
        duration_ms: int | None = None,
        **identity,
    ) -> TraceRecord:
        context = current_trace_context()
        with self._lock:
            record = TraceRecord(
                schema_version=1,
                daemon_id=self.daemon_id,
                sequence=next(self._sequence),
                timestamp=datetime.now(timezone.utc),
                monotonic_ns=time.monotonic_ns(),
                direction=TraceDirection(direction),
                layer=TraceLayer(layer),
                kind=kind,
                trace_id=identity.get("trace_id") or context.trace_id or uuid4().hex,
                request_id=identity.get("request_id", context.request_id),
                run_id=identity.get("run_id", context.run_id),
                execution_id=identity.get("execution_id", context.execution_id),
                slice_id=identity.get("slice_id", context.slice_id),
                span_id=identity.get("span_id", context.span_id),
                parent_span_id=identity.get("parent_span_id", context.parent_span_id),
                client_id=identity.get("client_id", context.client_id),
                duration_ms=duration_ms,
                data=sanitize_trace_data(data or {}),
            )
            try:
                # Keeping sequence allocation and non-blocking enqueue under the
                # same lock preserves file order across producer threads.
                self.writer.emit(record)
            except Exception as exc:
                debug_print("TRACE RECORD ERROR", str(exc))
        return record

    def flush(self) -> None:
        self.writer.flush()

    def close(self, timeout_seconds: float = 2.0) -> None:
        self.writer.close(timeout_seconds)


_recorder = NoopTraceRecorder()


def install_trace_recorder(recorder=None) -> None:
    global _recorder
    _recorder = recorder or NoopTraceRecorder()


def record_trace(direction, layer, kind: str, **kwargs):
    """Record one trace without exposing recorder ownership to business modules."""
    return _recorder.record(direction, layer, kind, **kwargs)
