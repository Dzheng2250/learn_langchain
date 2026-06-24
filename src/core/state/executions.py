"""Durable PendingExecution state and Slice accounting."""

from uuid import uuid4

from src.core.state.database import LocalStateDatabase
from src.core.state.execution_checkpoints import ExecutionCheckpointStore
from src.core.state.execution_models import (
    PendingExecution,
)
from src.core.state.execution_queries import ExecutionQueryStore
from src.core.state.execution_release import ExecutionReleaseStore
from src.core.state.execution_slices import ExecutionSliceStore
from src.core.state.types import CheckpointState, ExecutionStatus
from src.core.workspace.models import SessionContext


class ExecutionRepository:
    """Persist one-at-a-time recoverable executions per Session."""

    def __init__(self, database: LocalStateDatabase) -> None:
        self.database = database
        self.checkpoints = ExecutionCheckpointStore(database)
        self.queries = ExecutionQueryStore(database)
        self.release = ExecutionReleaseStore(database)
        self.slices = ExecutionSliceStore(database)

    def get_pending(self, session: SessionContext) -> PendingExecution | None:
        return self.queries.get_pending(session)

    def get_attached(self, session: SessionContext) -> PendingExecution | None:
        """Return any Execution still attached to the Session, including unrecoverable state."""
        return self.queries.get_attached(session)

    def begin(
        self,
        session: SessionContext,
        user_input: str,
        *,
        goal_mode: bool = False,
    ) -> PendingExecution:
        """Create a new recoverable execution only when the Session is idle."""
        execution_id, thread_id = uuid4().hex, uuid4().hex
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT pending_execution_id FROM sessions WHERE session_id = ?",
                (str(session.session_id),),
            ).fetchone()
            if row and row["pending_execution_id"]:
                raise RuntimeError(
                    "Session has a pending execution. Resume or discard it before starting a new chat."
                )
            conn.execute(
                """
                INSERT INTO executions(
                    execution_id, workspace_id, session_id, checkpoint_thread_id,
                    status, original_input, goal_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    str(session.workspace.workspace_id),
                    str(session.session_id),
                    thread_id,
                    ExecutionStatus.RUNNING,
                    user_input,
                    1 if goal_mode else 0,
                ),
            )
            conn.execute(
                """
                UPDATE sessions SET pending_execution_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ?
                """,
                (execution_id, str(session.session_id)),
            )
        return self.get_pending(session)

    def resume(self, session: SessionContext) -> PendingExecution:
        """Grant another bounded automatic execution batch."""
        pending = self.get_pending(session)
        if not pending:
            attached = self.get_attached(session)
            if attached is not None and not attached.recoverable:
                raise RuntimeError(
                    "Session execution cannot be resumed because its checkpoint is unavailable. "
                    "Inspect status and discard it before starting a new task."
                )
            raise RuntimeError("Session has no pending execution to resume.")
        with self.database.transaction() as conn:
            conn.execute(
                """
                UPDATE executions SET status=?, stop_reason='',
                    grant_index=grant_index+1, slice_index=0,
                    graph_steps_used=0, controlled_executions_used=0,
                    delegations_used=0, tool_calls_used=0,
                    updated_at=CURRENT_TIMESTAMP
                WHERE execution_id=?
                """,
                (ExecutionStatus.RUNNING, pending.execution_id),
            )
        return self.get_pending(session)

    def start_slice(self, execution_id: str, grant_index: int, slice_index: int) -> str:
        return self.slices.start_slice(execution_id, grant_index, slice_index)

    def finish_slice(
        self,
        slice_id: str,
        execution_id: str,
        *,
        status: ExecutionStatus | str,
        stop_reason: str,
        graph_steps_used: int = 0,
        usage: dict | None = None,
    ) -> None:
        """Finish one Slice and persist the latest Grant budget snapshot."""
        self.slices.finish_slice(
            slice_id,
            execution_id,
            status=status,
            stop_reason=stop_reason,
            graph_steps_used=graph_steps_used,
            usage=usage,
        )

    def pause(
        self,
        execution_id: str,
        status: ExecutionStatus | str,
        stop_reason: str,
        summary: str = "",
        *,
        usage: dict | None = None,
        checkpoint_state: CheckpointState | str = CheckpointState.AVAILABLE,
    ) -> None:
        """Persist a recoverable pause and the latest Grant budget counters."""
        usage = usage or {}
        status = ExecutionStatus(status)
        checkpoint_state = CheckpointState(checkpoint_state)
        with self.database.transaction() as conn:
            conn.execute(
                """
                UPDATE executions SET status=?, stop_reason=?, progress_summary=?, checkpoint_state=?,
                    controlled_executions_used=?, delegations_used=?, tool_calls_used=?,
                    updated_at=CURRENT_TIMESTAMP WHERE execution_id=?
                """,
                (
                    status,
                    stop_reason,
                    summary,
                    checkpoint_state,
                    int(usage.get("controlled_executions", 0)),
                    int(usage.get("delegations", 0)),
                    int(usage.get("tool_calls", 0)),
                    execution_id,
                ),
            )

    def complete(self, session: SessionContext, execution_id: str) -> None:
        self.release.complete(session, execution_id)

    def complete_in_transaction(self, conn, session: SessionContext, execution_id: str) -> None:
        self.release.complete_in_transaction(conn, session, execution_id)

    def finish_slice_in_transaction(
        self,
        conn,
        slice_id: str,
        execution_id: str,
        *,
        graph_steps_used: int,
        usage: dict,
    ) -> None:
        """Finish the successful final Slice inside the Turn transaction."""
        self.slices.finish_slice_in_transaction(
            conn,
            slice_id,
            execution_id,
            graph_steps_used=graph_steps_used,
            usage=usage,
        )

    def mark_checkpoint_cleaned(self, execution_id: str) -> None:
        self.checkpoints.mark_checkpoint_cleaned(execution_id)

    def mark_checkpoint_missing(self, execution_id: str) -> None:
        self.checkpoints.mark_checkpoint_missing(execution_id)

    def mark_paused_recovery(self, execution_id: str) -> None:
        self.checkpoints.mark_paused_recovery(execution_id)

    def list_for_recovery(self) -> list[dict]:
        """Return executions whose checkpoint relationship needs reconciliation."""
        return self.checkpoints.list_for_recovery()

    def discard(self, session: SessionContext) -> PendingExecution:
        return self.release.discard(session)

    def terminate(self, session: SessionContext, execution_id: str, reason: str) -> None:
        self.release.terminate(session, execution_id, reason)
