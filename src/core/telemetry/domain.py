"""Stable payload helpers for recurring domain events."""

from src.core.telemetry.models import TelemetryEvent
from src.core.telemetry.recorder import emit_event, record_error


def record_tool_started(
    source: str,
    tool: str | None,
    tool_call_id: str | None = None,
    args=None,
    message: str = "Tool call requested by LLM.",
) -> TelemetryEvent:
    """Record the observed ToolNode execution boundary."""
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
) -> TelemetryEvent:
    """Record a completed tool call with a bounded result preview."""
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
) -> TelemetryEvent:
    """Record a rejected or failed tool call."""
    details = dict(payload or {})
    details.update({"tool": tool, "tool_call_id": tool_call_id})
    if error is not None:
        return record_error(
            source,
            "tool",
            error,
            message,
            details,
            "tool_failed",
            duration_ms,
        )
    return emit_event(
        "tool_failed",
        source,
        message,
        details,
        level="error",
        duration_ms=duration_ms,
    )


def record_command_started(
    source: str,
    command: str,
    message: str = "Command requested.",
) -> TelemetryEvent:
    """Record the start of an internal shell or container command."""
    return emit_event("command_started", source, message, {"command_preview": command})


def record_command_finished(
    source: str,
    returncode: int,
    output: str = "",
    message: str = "Command finished.",
    duration_ms: int | None = None,
) -> TelemetryEvent:
    """Record a completed internal command."""
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
) -> TelemetryEvent:
    """Record an unavailable, rejected, or failed internal command."""
    return emit_event(
        "command_failed",
        source,
        message,
        {
            "reason": reason,
            "command_preview": command,
            "returncode": returncode,
            "detail_preview": detail,
        },
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
) -> TelemetryEvent:
    """Record a committed long-term memory change."""
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
