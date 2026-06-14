"""Ambient trace identity propagation across asyncio tasks and worker threads."""

from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from uuid import uuid4


@dataclass(frozen=True)
class TraceContext:
    trace_id: str = ""
    request_id: str | None = None
    run_id: str | None = None
    execution_id: str | None = None
    slice_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    client_id: str | None = None


_context: ContextVar[TraceContext] = ContextVar("trace_context", default=TraceContext())


def current_trace_context() -> TraceContext:
    return _context.get()


def bind_trace_context(**values) -> Token:
    """Replace supplied identity fields while preserving the current trace."""
    current = current_trace_context()
    if not values.get("trace_id") and not current.trace_id:
        values["trace_id"] = uuid4().hex
    return _context.set(replace(current, **values))


def new_trace_context(*, request_id=None, client_id=None) -> Token:
    """Start an independent request trace."""
    return _context.set(
        TraceContext(
            trace_id=uuid4().hex,
            request_id=str(request_id) if request_id is not None else None,
            client_id=client_id,
        )
    )


def reset_trace_context(token: Token) -> None:
    _context.reset(token)
