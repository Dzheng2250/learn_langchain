"""Domain models for durable Agent executions."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.state.types import CheckpointState, ExecutionStatus


ACTIVE_EXECUTION_STATUSES = tuple(status.value for status in ExecutionStatus.active())


@dataclass(frozen=True)
class PendingExecution:
    """Recoverable or attached execution currently owned by one Session."""

    execution_id: str
    checkpoint_thread_id: str
    status: ExecutionStatus
    stop_reason: str
    original_input: str
    progress_summary: str
    grant_index: int
    slice_index: int
    graph_steps_used: int
    controlled_executions_used: int
    delegations_used: int
    tool_calls_used: int
    checkpoint_state: CheckpointState
    goal_mode: bool = False
    resume_policy: str = "continue"
    pause_fingerprint: str = ""
    repeated_pause_count: int = 0

    @property
    def recoverable(self) -> bool:
        """Return whether a resume operation may safely use the checkpoint."""
        return (
            self.status in ACTIVE_EXECUTION_STATUSES
            and self.checkpoint_state == CheckpointState.AVAILABLE
        )


def pending_execution_from_row(row) -> PendingExecution:
    """Convert one SQLite execution row into the domain model."""
    return PendingExecution(
        execution_id=row["execution_id"],
        checkpoint_thread_id=row["checkpoint_thread_id"],
        status=ExecutionStatus(row["status"]),
        stop_reason=row["stop_reason"],
        original_input=row["original_input"],
        progress_summary=row["progress_summary"],
        grant_index=int(row["grant_index"]),
        slice_index=int(row["slice_index"]),
        graph_steps_used=int(row["graph_steps_used"]),
        controlled_executions_used=int(row["controlled_executions_used"]),
        delegations_used=int(row["delegations_used"]),
        tool_calls_used=int(row["tool_calls_used"]),
        checkpoint_state=CheckpointState(row["checkpoint_state"]),
        goal_mode=bool(row["goal_mode"]) if "goal_mode" in row.keys() else False,
        resume_policy=(
            str(row["resume_policy"])
            if "resume_policy" in row.keys() and row["resume_policy"]
            else "continue"
        ),
        pause_fingerprint=(
            str(row["pause_fingerprint"])
            if "pause_fingerprint" in row.keys() else ""
        ),
        repeated_pause_count=(
            int(row["repeated_pause_count"])
            if "repeated_pause_count" in row.keys() else 0
        ),
    )
