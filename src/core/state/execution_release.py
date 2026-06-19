"""Execution completion and Session release operations."""

from src.core.state.database import LocalStateDatabase
from src.core.state.execution_models import PendingExecution, pending_execution_from_row
from src.core.state.types import CheckpointState, ExecutionStatus
from src.core.workspace.models import SessionContext


class ExecutionReleaseStore:
    """Persist terminal Execution transitions that release a Session."""

    def __init__(self, database: LocalStateDatabase) -> None:
        self.database = database

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
        self._release_session(
            conn,
            session,
            execution_id,
            error_message="Completed Turn did not clear exactly one pending Session Execution.",
        )

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
            pending = pending_execution_from_row(row)
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
            self._release_session(
                conn,
                session,
                pending.execution_id,
                error_message="Discard did not release exactly one Session.",
            )
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
            self._release_session(
                conn,
                session,
                execution_id,
                error_message="Terminal error did not release exactly one Session.",
            )

    def _release_session(
        self,
        conn,
        session: SessionContext,
        execution_id: str,
        *,
        error_message: str,
    ) -> None:
        session_update = conn.execute(
            """
            UPDATE sessions SET pending_execution_id=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE session_id=? AND pending_execution_id=?
            """,
            (str(session.session_id), execution_id),
        )
        if session_update.rowcount != 1:
            raise RuntimeError(error_message)

