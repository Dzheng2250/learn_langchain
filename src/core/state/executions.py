"""Durable PendingExecution state and Slice accounting."""

from dataclasses import dataclass
from uuid import uuid4

from src.core.state.database import LocalStateDatabase
from src.core.state.types import CheckpointState, ExecutionStatus
from src.core.workspace.models import SessionContext


ACTIVE_EXECUTION_STATUSES = tuple(status.value for status in ExecutionStatus.active())


@dataclass(frozen=True)
class PendingExecution:
    """Recoverable execution currently owned by one Session."""

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

    @property
    def recoverable(self) -> bool:
        """Return whether a resume operation may safely use the checkpoint."""
        return (
            self.status in ACTIVE_EXECUTION_STATUSES
            and self.checkpoint_state == CheckpointState.AVAILABLE
        )


class ExecutionRepository:
    """Persist one-at-a-time recoverable executions per Session."""

    def __init__(self, database: LocalStateDatabase) -> None:
        self.database = database

    def get_pending(self, session: SessionContext) -> PendingExecution | None:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT e.* FROM sessions s
                JOIN executions e ON e.execution_id = s.pending_execution_id
                WHERE s.workspace_id = ? AND s.session_id = ?
                  AND e.status IN (?, ?, ?, ?, ?)
                """,
                (
                    str(session.workspace.workspace_id),
                    str(session.session_id),
                    *ACTIVE_EXECUTION_STATUSES,
                ),
            ).fetchone()
        return self._from_row(row) if row else None

    def get_attached(self, session: SessionContext) -> PendingExecution | None:
        """Return any Execution still attached to the Session, including unrecoverable state."""
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT e.* FROM sessions s
                JOIN executions e ON e.execution_id = s.pending_execution_id
                WHERE s.workspace_id=? AND s.session_id=?
                """,
                (str(session.workspace.workspace_id), str(session.session_id)),
            ).fetchone()
        return self._from_row(row) if row else None

    def begin(self, session: SessionContext, user_input: str) -> PendingExecution:
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
                    status, original_input
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    str(session.workspace.workspace_id),
                    str(session.session_id),
                    thread_id,
                    ExecutionStatus.RUNNING,
                    user_input,
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
        slice_id = uuid4().hex
        with self.database.transaction() as conn:
            conn.execute(
                """
                INSERT INTO execution_slices(slice_id, execution_id, grant_index, slice_index, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (slice_id, execution_id, grant_index, slice_index, ExecutionStatus.RUNNING),
            )
            conn.execute(
                """
                UPDATE executions SET slice_index=?, status=?, updated_at=CURRENT_TIMESTAMP
                WHERE execution_id=?
                """,
                (slice_index, ExecutionStatus.RUNNING, execution_id),
            )
        return slice_id

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
        usage = usage or {}
        status = ExecutionStatus(status)
        with self.database.transaction() as conn:
            conn.execute(
                """
                UPDATE execution_slices SET status=?, stop_reason=?, graph_steps_used=?,
                    finished_at=CURRENT_TIMESTAMP
                WHERE slice_id=?
                """,
                (status, stop_reason, graph_steps_used, slice_id),
            )
            conn.execute(
                """
                UPDATE executions SET status=?, stop_reason=?,
                    graph_steps_used=graph_steps_used + ?,
                    controlled_executions_used=?,
                    delegations_used=?,
                    tool_calls_used=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE execution_id=?
                """,
                (
                    status,
                    stop_reason,
                    graph_steps_used,
                    int(usage.get("controlled_executions", 0)),
                    int(usage.get("delegations", 0)),
                    int(usage.get("tool_calls", 0)),
                    execution_id,
                ),
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
        with self.database.transaction() as conn:
            self.complete_in_transaction(conn, session, execution_id)

    def complete_in_transaction(self, conn, session: SessionContext, execution_id: str) -> None:
        """Mark an Execution complete inside the caller's Turn transaction."""
        execution_update = conn.execute(
            """
            UPDATE executions
            SET status=?, stop_reason=?,
                checkpoint_state=?, completed_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            WHERE execution_id=?
            """,
            (
                ExecutionStatus.COMPLETED,
                ExecutionStatus.COMPLETED,
                CheckpointState.CLEANUP_PENDING,
                execution_id,
            ),
        )
        if execution_update.rowcount != 1:
            raise RuntimeError("Completed Turn did not update exactly one Execution.")
        session_update = conn.execute(
            """
            UPDATE sessions SET pending_execution_id=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE session_id=? AND pending_execution_id=?
            """,
            (str(session.session_id), execution_id),
        )
        if session_update.rowcount != 1:
            raise RuntimeError("Completed Turn did not clear exactly one pending Session Execution.")

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
        slice_update = conn.execute(
            """
            UPDATE execution_slices
            SET status=?, stop_reason=?, graph_steps_used=?,
                finished_at=CURRENT_TIMESTAMP
            WHERE slice_id=?
            """,
            (
                ExecutionStatus.COMPLETED,
                ExecutionStatus.COMPLETED,
                graph_steps_used,
                slice_id,
            ),
        )
        if slice_update.rowcount != 1:
            raise RuntimeError("Completed Turn did not finish exactly one Execution Slice.")
        execution_update = conn.execute(
            """
            UPDATE executions
            SET graph_steps_used=graph_steps_used + ?, controlled_executions_used=?,
                delegations_used=?, tool_calls_used=?, updated_at=CURRENT_TIMESTAMP
            WHERE execution_id=?
            """,
            (
                graph_steps_used,
                int(usage.get("controlled_executions", 0)),
                int(usage.get("delegations", 0)),
                int(usage.get("tool_calls", 0)),
                execution_id,
            ),
        )
        if execution_update.rowcount != 1:
            raise RuntimeError("Completed Slice did not update exactly one Execution.")

    def mark_checkpoint_cleaned(self, execution_id: str) -> None:
        with self.database.transaction() as conn:
            conn.execute(
                """
                UPDATE executions SET checkpoint_state=?, updated_at=CURRENT_TIMESTAMP
                WHERE execution_id=?
                """,
                (CheckpointState.CLEANED, execution_id),
            )

    def mark_checkpoint_missing(self, execution_id: str) -> None:
        with self.database.transaction() as conn:
            conn.execute(
                """
                UPDATE executions
                SET status=?, checkpoint_state=?,
                    stop_reason='checkpoint_missing', updated_at=CURRENT_TIMESTAMP
                WHERE execution_id=?
                """,
                (
                    ExecutionStatus.UNRECOVERABLE_CHECKPOINT,
                    CheckpointState.MISSING,
                    execution_id,
                ),
            )

    def mark_paused_recovery(self, execution_id: str) -> None:
        with self.database.transaction() as conn:
            conn.execute(
                """
                UPDATE executions
                SET status=?, checkpoint_state=?,
                    stop_reason='core_restarted', updated_at=CURRENT_TIMESTAMP
                WHERE execution_id=?
                """,
                (
                    ExecutionStatus.PAUSED_RECOVERY,
                    CheckpointState.AVAILABLE,
                    execution_id,
                ),
            )

    def list_for_recovery(self) -> list[dict]:
        """Return executions whose checkpoint relationship needs reconciliation."""
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT execution_id, workspace_id, session_id, checkpoint_thread_id,
                       status, checkpoint_state
                FROM executions
                WHERE status IN (?, ?, ?, ?, ?)
                   OR (
                       status IN (?, ?)
                       AND checkpoint_state != ?
                   )
                """,
                (
                    *ACTIVE_EXECUTION_STATUSES,
                    ExecutionStatus.COMPLETED,
                    ExecutionStatus.DISCARDED,
                    CheckpointState.CLEANED,
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def discard(self, session: SessionContext) -> PendingExecution:
        with self.database.transaction() as conn:
            row = conn.execute(
                """
                SELECT e.* FROM sessions s
                JOIN executions e ON e.execution_id = s.pending_execution_id
                WHERE s.workspace_id=? AND s.session_id=?
                """,
                (str(session.workspace.workspace_id), str(session.session_id)),
            ).fetchone()
            if not row:
                raise RuntimeError("Session has no pending execution to discard.")
            pending = self._from_row(row)
            execution_update = conn.execute(
                """
                UPDATE executions SET status=?, stop_reason=?,
                    checkpoint_state=?, completed_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP WHERE execution_id=?
                """,
                (
                    ExecutionStatus.DISCARDED,
                    ExecutionStatus.DISCARDED,
                    CheckpointState.CLEANUP_PENDING,
                    pending.execution_id,
                ),
            )
            if execution_update.rowcount != 1:
                raise RuntimeError("Discard did not update exactly one Execution.")
            session_update = conn.execute(
                """
                UPDATE sessions SET pending_execution_id=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE session_id=? AND pending_execution_id=?
                """,
                (str(session.session_id), pending.execution_id),
            )
            if session_update.rowcount != 1:
                raise RuntimeError("Discard did not release exactly one Session.")
        return pending

    def terminate(self, session: SessionContext, execution_id: str, reason: str) -> None:
        """Terminate a non-retryable execution and release its Session atomically."""
        with self.database.transaction() as conn:
            execution_update = conn.execute(
                """
                UPDATE executions
                SET status=?, stop_reason=?, progress_summary=?,
                    original_input='[REDACTED]',
                    checkpoint_state=?, completed_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE execution_id=? AND session_id=?
                """,
                (
                    ExecutionStatus.DISCARDED,
                    reason,
                    "Execution terminated after a non-retryable provider error.",
                    CheckpointState.CLEANUP_PENDING,
                    execution_id,
                    str(session.session_id),
                ),
            )
            if execution_update.rowcount != 1:
                raise RuntimeError("Terminal error did not update exactly one Execution.")
            session_update = conn.execute(
                """
                UPDATE sessions SET pending_execution_id=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE session_id=? AND pending_execution_id=?
                """,
                (str(session.session_id), execution_id),
            )
            if session_update.rowcount != 1:
                raise RuntimeError("Terminal error did not release exactly one Session.")

    def _from_row(self, row) -> PendingExecution:
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
        )
