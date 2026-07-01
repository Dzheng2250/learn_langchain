"""LangChain tool factories for private task planning."""

from typing import Any

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime

from src.core.tasks.context import ToolExecutionContext
from src.core.tasks.service import TaskPlanningService


def _context_from_runtime(runtime: ToolRuntime) -> ToolExecutionContext:
    """Read the graph-injected Execution identity from LangGraph runtime."""
    context = getattr(runtime, "context", None)
    # LangGraph's ToolRuntime is a TypedDict (dict subclass). In Python 3.12
    # getattr does not read TypedDict keys (added in Python 3.13 via PEP 705),
    # so fall back to dict access when getattr returns None.
    if context is None and isinstance(runtime, dict):
        context = runtime.get("context")
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
        values from the same Execution.
        """
        try:
            return task_service.plan(_context_from_runtime(runtime), tasks)
        except ValueError as exc:
            return _tool_error(exc)

    @tool
    def task_update(
        task_key: str,
        runtime: ToolRuntime,
        status: str | None = None,
        subject: str | None = None,
        description: str | None = None,
        notes: str | None = None,
        depends_on: list[str] | None = None,
    ) -> str:
        """Update one private task by semantic task_key."""
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
