"""Execution identity guard for private task operations."""

from src.core.tasks.context import ToolExecutionContext


class TaskExecutionContextGuard:
    """Validate that task tools can only touch the active Execution."""

    def assert_matches(self, conn, context: ToolExecutionContext) -> None:
        """Raise when graph runtime context does not match a persisted Execution."""
        execution_id = context.require_execution_id()
        row = conn.execute(
            """
            SELECT 1 FROM executions
            WHERE execution_id=? AND workspace_id=? AND session_id=?
            """,
            (execution_id, context.workspace_id, context.session_id),
        ).fetchone()
        if row is None:
            raise ValueError("Task context does not match an active Execution.")
