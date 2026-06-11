import time
from contextlib import contextmanager
from contextvars import ContextVar

from src.config.settings import (
    AGENT_EVENTS_CONSOLE_ENABLED,
    AGENT_EVENTS_ENABLED,
    AGENT_EVENTS_FILE_ENABLED,
    AGENT_EVENTS_POSTGRES_ENABLED,
)
from src.core.common.debug import debug_print
from src.core.hooks.models import AgentEvent, AgentEventContext, EventSink, HookHelperSpec
from src.core.hooks.serialization import event_to_dict, sanitize_payload
from src.core.hooks.sinks import ConsoleEventSink, JsonlFileEventSink, NoopEventSink, PostgresEventSink

_event_context: ContextVar[AgentEventContext] = ContextVar(
    "agent_event_context",
    default=AgentEventContext(),
)
_event_sinks: list[EventSink] | None = None


def set_event_context(
    session_id=None,
    turn_index: int | None = None,
    run_id: str = "",
    workspace_id=None,
) -> None:
    """Set context used by subsequent emitted events."""
    _event_context.set(
        AgentEventContext(
            workspace_id=workspace_id,
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
        workspace_id=context.workspace_id,
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


