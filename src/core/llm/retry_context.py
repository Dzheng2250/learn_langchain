"""Request-local retry identity and foreground event propagation."""

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Callable

RetryEventCallback = Callable[[dict], None]


@dataclass
class LlmAttemptContext:
    """Mutable state for one model attempt inside an isolated ContextVar."""

    attempt_id: str
    output_emitted: bool = False


_event_callback: ContextVar[RetryEventCallback | None] = ContextVar(
    "llm_retry_event_callback", default=None
)
_attempt: ContextVar[LlmAttemptContext | None] = ContextVar(
    "llm_attempt_context", default=None
)


def bind_retry_event_callback(callback: RetryEventCallback | None) -> Token:
    return _event_callback.set(callback)


def reset_retry_event_callback(token: Token) -> None:
    _event_callback.reset(token)


def emit_retry_event(event: str, data: dict) -> None:
    """Compatibility name for emitting any request-local foreground event."""
    emit_foreground_event(event, data)


def emit_foreground_event(event: str, data: dict) -> None:
    """Deliver one Core event to the active request without coupling its producer."""
    callback = _event_callback.get()
    if callback is not None:
        callback({"event": event, "data": data})


def bind_attempt(attempt_id: str) -> Token:
    return _attempt.set(LlmAttemptContext(attempt_id))


def reset_attempt(token: Token) -> None:
    _attempt.reset(token)


def current_attempt_id() -> str | None:
    attempt = _attempt.get()
    return attempt.attempt_id if attempt else None


def mark_attempt_output_emitted() -> None:
    attempt = _attempt.get()
    if attempt is not None:
        attempt.output_emitted = True


def current_attempt_has_output() -> bool:
    attempt = _attempt.get()
    return bool(attempt and attempt.output_emitted)
