"""Factories for stable Agent stream events returned to RPC clients."""

from src.core.agent.models import StopReason
from src.core.workspace.models import SessionContext


def pending_execution_event(session: SessionContext, run_id: str, pending) -> dict:
    """Build the terminal event for a Session blocked by recoverable work."""
    message = (
        "Session has a pending execution. Use 'learn-agent session resume --session "
        f"{session.session_name}' to continue, or 'learn-agent session discard --session "
        f"{session.session_name}' to discard it before starting a new chat."
    )
    return {
        "event": "done",
        "data": {
            "run_id": run_id,
            "status": "paused",
            "workspace_id": str(session.workspace.workspace_id),
            "session_id": str(session.session_id),
            "session_name": session.session_name,
            "execution_id": pending.execution_id,
            "stop_reason": pending.stop_reason or pending.status.value,
            "goal_mode": pending.goal_mode,
            "message": message,
        },
    }


def archived_session_event(session: SessionContext, run_id: str) -> dict:
    """Build the terminal event for an intentionally unavailable Session."""
    return {
        "event": "done",
        "data": {
            "run_id": run_id,
            "status": "archived",
            "workspace_id": str(session.workspace.workspace_id),
            "session_id": str(session.session_id),
            "session_name": session.session_name,
            "message": (
                "Session is archived. Use a different session name, or run "
                "`learn-agent session delete --hard` before recreating it."
            ),
        },
    }


def idle_resume_event(session: SessionContext, run_id: str) -> dict:
    """Build the terminal event for resume when no pending execution exists."""
    return {
        "event": "done",
        "data": {
            "run_id": run_id,
            "status": "idle",
            "workspace_id": str(session.workspace.workspace_id),
            "session_id": str(session.session_id),
            "session_name": session.session_name,
            "message": "Session has no pending execution to resume.",
        },
    }


def completed_turn_event(
    *,
    session: SessionContext,
    run_id: str,
    execution,
    tool_call_count: int,
    slices_used: int,
    finalization,
    context_tokens: int,
) -> dict:
    """Build the terminal event for a successfully committed turn."""
    return {
        "event": "done",
        "data": {
            "run_id": run_id,
            "status": "ok",
            "workspace_id": str(session.workspace.workspace_id),
            "session_id": str(session.session_id),
            "session_name": session.session_name,
            "execution_id": execution.execution_id if execution else None,
            "stop_reason": StopReason.COMPLETED.value,
            "tool_call_count": tool_call_count,
            "slices_used": slices_used,
            "goal_mode": bool(getattr(execution, "goal_mode", False)),
            "durability": "committed",
            "maintenance_status": finalization.maintenance_status,
            "memory_status": finalization.memory_status,
            "memory_request_explicit": finalization.memory_request_explicit,
            "context_tokens": context_tokens,
        },
    }


def paused_turn_event(
    *,
    session: SessionContext,
    run_id: str,
    execution,
    stop_reason: str,
    tool_call_count: int,
    slices_used: int,
    message: str,
) -> dict:
    """Build the terminal event for a paused recoverable execution."""
    return {
        "event": "done",
        "data": {
            "run_id": run_id,
            "status": "paused",
            "workspace_id": str(session.workspace.workspace_id),
            "session_id": str(session.session_id),
            "session_name": session.session_name,
            "execution_id": execution.execution_id if execution else None,
            "stop_reason": stop_reason,
            "tool_call_count": tool_call_count,
            "slices_used": slices_used,
            "goal_mode": bool(getattr(execution, "goal_mode", False)),
            "message": message,
        },
    }


def failed_turn_event(run_id: str, message: str) -> dict:
    """Build the terminal error event for unexpected turn failures."""
    return {
        "event": "error",
        "data": {
            "type": "turn_failed",
            "stop_reason": StopReason.TURN_ERROR.value,
            "message": message,
            "run_id": run_id,
        },
    }
