"""LangChain tool factories for private task planning."""

from typing import Any, Literal

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime

from src.core.tasks.context import ToolExecutionContext
from src.core.tasks.service import TaskPlanningService


def _context_from_runtime(runtime: ToolRuntime) -> ToolExecutionContext:
    """Read the graph-injected Execution identity from LangGraph runtime."""
    context = getattr(runtime, "context", None)
    if isinstance(context, ToolExecutionContext):
        return context
    if isinstance(context, dict):
        return ToolExecutionContext(
            workspace_id=str(context.get("workspace_id", "")),
            session_id=str(context.get("session_id", "")),
            execution_id=context.get("execution_id"),
        )
    raise ValueError("Task tools require graph runtime context.")


def _tool_error(exc: Exception) -> str:
    """Return a deterministic tool-facing validation error."""
    return f"Task tool error: {exc}"


def create_task_tools(task_service: TaskPlanningService) -> list:
    """Create parent-only tools backed by one TaskPlanningService."""

    @tool
    def task_plan(tasks: list[dict[str, Any]], runtime: ToolRuntime) -> str:
        """Create or update a private Execution task plan with semantic task keys.

        Each task dictionary must include task_key and subject. Optional fields:
        description, notes, depends_on. Dependencies must reference task_key
        values from the same Execution. After creating a plan, keep every task's
        status current with task_update as work starts, completes, or becomes
        blocked.
        """
        try:
            return task_service.plan(_context_from_runtime(runtime), tasks)
        except ValueError as exc:
            return _tool_error(exc)

    @tool
    def task_update(
        task_key: str,
        status: Literal["pending", "in_progress", "completed", "cancelled"],
        runtime: ToolRuntime,
        subject: str | None = None,
        description: str | None = None,
        notes: str | None = None,
        depends_on: list[str] | None = None,
    ) -> str:
        """Update one task and return the complete latest plan for progress display.

        Mark work in_progress when starting it and completed immediately after its
        work and validation succeed. Use notes to record a genuine blocker.
        """
        try:
            return task_service.update(
                _context_from_runtime(runtime),
                task_key,
                status=status,
                subject=subject,
                description=description,
                notes=notes,
                depends_on=depends_on,
            )
        except ValueError as exc:
            return _tool_error(exc)

    @tool
    def task_list(runtime: ToolRuntime) -> str:
        """List the active Execution's private task plan."""
        try:
            return task_service.list(_context_from_runtime(runtime))
        except ValueError as exc:
            return _tool_error(exc)

    @tool
    def task_get(task_key: str, runtime: ToolRuntime) -> str:
        """Read one private task by semantic task_key."""
        try:
            return task_service.get(_context_from_runtime(runtime), task_key)
        except ValueError as exc:
            return _tool_error(exc)

    return [task_plan, task_update, task_list, task_get]
