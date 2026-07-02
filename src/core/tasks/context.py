"""Runtime identity injected into private task tools."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolExecutionContext:
    """Workspace, Session, and Execution identity for one graph invocation."""

    workspace_id: str
    session_id: str
    execution_id: str | None = None
    run_id: str | None = None
    actor: str = "parent"
    workspace_root: str = ""
    turn_index: int | None = None
    slice_id: str | None = None

    def require_execution_id(self) -> str:
        """Return the active Execution ID or reject task access."""
        if not self.execution_id:
            raise ValueError("Task tools require an active Execution context.")
        return self.execution_id
