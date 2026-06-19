"""Read-only queries for durable Execution state."""

from src.core.state.database import LocalStateDatabase
from src.core.state.execution_models import (
    ACTIVE_EXECUTION_STATUSES,
    PendingExecution,
    pending_execution_from_row,
)
from src.core.workspace.models import SessionContext


class ExecutionQueryStore:
    """Load attached or recoverable Execution rows for one Session."""

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
        return pending_execution_from_row(row) if row else None

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
        return pending_execution_from_row(row) if row else None
