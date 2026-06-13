"""Durable PendingExecution state and Slice accounting."""

from dataclasses import dataclass
from uuid import uuid4

from src.core.state.database import LocalStateDatabase
from src.core.workspace.models import SessionContext


ACTIVE_EXECUTION_STATUSES = ("running", "paused_budget", "paused_error", "paused_confirmation")


@dataclass(frozen=True)
class PendingExecution:
    """Recoverable execution currently owned by one Session."""

    execution_id: str
    checkpoint_thread_id: str
    status: str
    stop_reason: str
    original_input: str
    progress_summary: str
    grant_index: int
    slice_index: int
    graph_steps_used: int
    controlled_executions_used: int
    delegations_used: int
    tool_calls_used: int


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
                  AND e.status IN ('running', 'paused_budget', 'paused_error', 'paused_confirmation')
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
                ) VALUES (?, ?, ?, ?, 'running', ?)
                """,
                (
                    execution_id,
                    str(session.workspace.workspace_id),
                    str(session.session_id),
                    thread_id,
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
            raise RuntimeError("Session has no pending execution to resume.")
        with self.database.transaction() as conn:
            conn.execute(
                """
                UPDATE executions SET status='running', stop_reason='',
                    grant_index=grant_index+1, slice_index=0,
                    graph_steps_used=0, controlled_executions_used=0,
                    delegations_used=0, tool_calls_used=0,
                    updated_at=CURRENT_TIMESTAMP
                WHERE execution_id=?
                """,
                (pending.execution_id,),
            )
        return self.get_pending(session)

    def start_slice(self, execution_id: str, grant_index: int, slice_index: int) -> str:
        slice_id = uuid4().hex
        with self.database.transaction() as conn:
            conn.execute(
                """
                INSERT INTO execution_slices(slice_id, execution_id, grant_index, slice_index, status)
                VALUES (?, ?, ?, ?, 'running')
                """,
                (slice_id, execution_id, grant_index, slice_index),
            )
            conn.execute(
                """
                UPDATE executions SET slice_index=?, status='running', updated_at=CURRENT_TIMESTAMP
                WHERE execution_id=?
                """,
                (slice_index, execution_id),
            )
        return slice_id

    def finish_slice(
        self,
        slice_id: str,
        execution_id: str,
        *,
        status: str,
        stop_reason: str,
        graph_steps_used: int = 0,
        usage: dict | None = None,
    ) -> None:
        """Finish one Slice and persist the latest Grant budget snapshot."""
        usage = usage or {}
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
        status: str,
        stop_reason: str,
        summary: str = "",
        *,
        usage: dict | None = None,
    ) -> None:
        """Persist a recoverable pause and the latest Grant budget counters."""
        usage = usage or {}
        with self.database.transaction() as conn:
            conn.execute(
                """
                UPDATE executions SET status=?, stop_reason=?, progress_summary=?,
                    controlled_executions_used=?, delegations_used=?, tool_calls_used=?,
                    updated_at=CURRENT_TIMESTAMP WHERE execution_id=?
                """,
                (
                    status,
                    stop_reason,
                    summary,
                    int(usage.get("controlled_executions", 0)),
                    int(usage.get("delegations", 0)),
                    int(usage.get("tool_calls", 0)),
                    execution_id,
                ),
            )

    def complete(self, session: SessionContext, execution_id: str) -> None:
        with self.database.transaction() as conn:
            conn.execute(
                "UPDATE executions SET status='completed', stop_reason='completed', updated_at=CURRENT_TIMESTAMP "
                "WHERE execution_id=?",
                (execution_id,),
            )
            conn.execute(
                """
                UPDATE sessions SET pending_execution_id=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE session_id=? AND pending_execution_id=?
                """,
                (str(session.session_id), execution_id),
            )

    def discard(self, session: SessionContext) -> PendingExecution:
        pending = self.get_pending(session)
        if not pending:
            raise RuntimeError("Session has no pending execution to discard.")
        with self.database.transaction() as conn:
            conn.execute(
                """
                UPDATE executions SET status='discarded', stop_reason='discarded',
                    updated_at=CURRENT_TIMESTAMP WHERE execution_id=?
                """,
                (pending.execution_id,),
            )
            conn.execute(
                "UPDATE sessions SET pending_execution_id=NULL, updated_at=CURRENT_TIMESTAMP WHERE session_id=?",
                (str(session.session_id),),
            )
        return pending

    def _from_row(self, row) -> PendingExecution:
        return PendingExecution(
            execution_id=row["execution_id"],
            checkpoint_thread_id=row["checkpoint_thread_id"],
            status=row["status"],
            stop_reason=row["stop_reason"],
            original_input=row["original_input"],
            progress_summary=row["progress_summary"],
            grant_index=int(row["grant_index"]),
            slice_index=int(row["slice_index"]),
            graph_steps_used=int(row["graph_steps_used"]),
            controlled_executions_used=int(row["controlled_executions_used"]),
            delegations_used=int(row["delegations_used"]),
            tool_calls_used=int(row["tool_calls_used"]),
        )
