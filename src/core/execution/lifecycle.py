"""Execution state transitions used by foreground Agent turns."""

from dataclasses import dataclass
from typing import Any

from src.core.agent.models import StopReason
from src.core.ports import ExecutionLifecycleStore
from src.core.state.types import ExecutionStatus
from src.core.tracing import TraceDirection, TraceLayer, record_trace


@dataclass(frozen=True)
class ExecutionStart:
    """Result of attempting to attach an Execution to a new user turn."""

    execution: Any | None = None
    pending: Any | None = None

    @property
    def blocked_by_pending(self) -> bool:
        """Return whether an existing recoverable Execution blocked this turn."""
        return self.pending is not None


class ExecutionLifecycleService:
    """Coordinate Execution begin/resume/pause operations.

    This service keeps Execution state-machine decisions out of
    `AgentTurnService`. The repository remains the durable authority; this
    class only decides which repository operation belongs to the current
    application path and emits the corresponding trace records.
    """

    def __init__(self, execution_store: ExecutionLifecycleStore | None) -> None:
        self.execution_store = execution_store

    def begin_turn(self, session, user_input: str, *, goal_mode: bool) -> ExecutionStart:
        """Attach a new Execution unless the Session already has pending work."""
        if self.execution_store is None:
            return ExecutionStart()
        pending = self.execution_store.get_pending(session)
        if pending is not None:
            return ExecutionStart(pending=pending)
        execution = self.execution_store.begin(
            session,
            user_input,
            goal_mode=goal_mode,
        )
        self._record_attached(execution, {"status": execution.status.value, "goal_mode": goal_mode})
        return ExecutionStart(execution=execution)

    def has_attached_execution(self, session) -> bool:
        """Return whether the Session has any attached Execution state."""
        if self.execution_store is None:
            return False
        return self.execution_store.get_attached(session) is not None

    def resume(self, session):
        """Resume the Session's recoverable Execution and record trace identity."""
        if self.execution_store is None:
            raise RuntimeError("Resumable execution is not configured.")
        pending = self.execution_store.resume(session)
        self._record_attached(
            pending,
            {
                "status": pending.status.value,
                "resume": True,
                "goal_mode": pending.goal_mode,
            },
        )
        return pending

    def pause_runtime_creation_failed(self, execution, exc: Exception) -> None:
        """Persist a recoverable pause when workspace runtime creation fails."""
        self._pause_error(execution, f"Workspace runtime creation failed: {exc}")

    def pause_resume_preparation_failed(self, execution, exc: Exception) -> None:
        """Persist a recoverable pause when resume graph preparation fails."""
        self._pause_error(execution, f"Execution resume preparation failed: {exc}")

    def _pause_error(self, execution, summary: str) -> None:
        if execution is None or self.execution_store is None:
            return
        self.execution_store.pause(
            execution.execution_id,
            ExecutionStatus.PAUSED_ERROR,
            StopReason.TURN_ERROR.value,
            summary,
        )

    def _record_attached(self, execution, data: dict) -> None:
        record_trace(
            TraceDirection.INTERNAL,
            TraceLayer.AGENT,
            "agent.execution_attached",
            execution_id=execution.execution_id,
            data=data,
        )
