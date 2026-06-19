"""Checkpoint reconciliation operations for durable Executions."""

from src.core.state.database import LocalStateDatabase
from src.core.state.execution_models import ACTIVE_EXECUTION_STATUSES
from src.core.state.types import CheckpointState, ExecutionStatus


class ExecutionCheckpointStore:
    """Persist and query the checkpoint side of Execution recovery state."""

    def __init__(self, database: LocalStateDatabase) -> None:
        self.database = database

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
