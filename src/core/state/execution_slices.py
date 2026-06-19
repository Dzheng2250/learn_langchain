"""Slice persistence and budget accounting for durable Executions."""

from uuid import uuid4

from src.core.state.database import LocalStateDatabase
from src.core.state.types import ExecutionStatus


class ExecutionSliceStore:
    """Persist bounded LangGraph Slice state for one Execution."""

    def __init__(self, database: LocalStateDatabase) -> None:
        self.database = database

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
