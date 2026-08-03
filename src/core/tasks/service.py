"""Application service for parent Agent private task tools."""

from collections.abc import Iterable

from src.config.tasks import TaskSettings
from src.core.tasks.context import ToolExecutionContext
from src.core.tasks.models import ExecutionTask, TaskPlanItem, TaskStatus
from src.core.tasks.repository import TaskRepository


class TaskPlanningService:
    """Validate tool-facing payloads and format compact task summaries."""

    def __init__(
        self,
        repository: TaskRepository,
        settings: TaskSettings | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings or repository.settings

    def plan(self, context: ToolExecutionContext, tasks: list[dict]) -> str:
        """Create or update a private plan for the active Execution."""
        items = [
            TaskPlanItem(
                task_key=str(raw.get("task_key", "")).strip(),
                subject=str(raw.get("subject", "")).strip(),
                description=str(raw.get("description", "")).strip(),
                notes=str(raw.get("notes", "")).strip(),
                depends_on=tuple(
                    dict.fromkeys(
                        str(dep).strip()
                        for dep in raw.get("depends_on", [])
                        if str(dep).strip()
                    )
                ),
                ordinal=index,
            )
            for index, raw in enumerate(tasks, start=1)
        ]
        planned = self.repository.plan(context, items)
        return self._bounded(
            "Task plan saved.\n" + self._format_list(planned),
            self.settings.list_output_limit,
        )

    def update(
        self,
        context: ToolExecutionContext,
        task_key: str,
        *,
        status: str | None = None,
        subject: str | None = None,
        description: str | None = None,
        notes: str | None = None,
        depends_on: list[str] | None = None,
    ) -> str:
        """Update one private task and return its latest status."""
        if all(
            value is None
            for value in (status, subject, description, notes, depends_on)
        ):
            raise ValueError(
                "task_update requires a change; provide status for progress updates"
            )
        task = self.repository.update(
            context,
            task_key.strip(),
            status=TaskStatus(status) if status else None,
            subject=subject.strip() if isinstance(subject, str) else None,
            description=description.strip() if isinstance(description, str) else None,
            notes=notes.strip() if isinstance(notes, str) else None,
            depends_on=depends_on,
        )
        tasks = self.repository.list(context)
        return self._bounded(
            "Task updated: " + task.task_key + "\n" + self._format_list(tasks),
            self.settings.list_output_limit,
        )

    def list(self, context: ToolExecutionContext) -> str:
        """Return a compact current plan view for the active Execution."""
        tasks = self.repository.list(context)
        if not tasks:
            return "No private task plan exists for this Execution."
        return self._bounded(self._format_list(tasks), self.settings.list_output_limit)

    def has_unfinished(self, context: ToolExecutionContext) -> bool:
        """Return whether an Execution plan contains actionable unfinished work."""
        return any(
            task.status in {TaskStatus.PENDING, TaskStatus.IN_PROGRESS}
            for task in self.repository.list(context)
        )

    def get(self, context: ToolExecutionContext, task_key: str) -> str:
        """Return one task's full private planning details."""
        task = self.repository.get(context, task_key.strip())
        if task is None:
            return f"Task not found: {task_key}"
        return self._bounded(self._format_task(task, verbose=True), self.settings.list_output_limit)

    def _format_list(self, tasks: Iterable[ExecutionTask]) -> str:
        lines = []
        for task in tasks:
            marker = {
                TaskStatus.PENDING: "[ ]",
                TaskStatus.IN_PROGRESS: "[>]",
                TaskStatus.COMPLETED: "[x]",
                TaskStatus.CANCELLED: "[-]",
            }[task.status]
            state = self._display_state(task)
            lines.append(f"{marker} {task.task_key}: {task.subject} ({state})")
        return "\n".join(lines)

    def _format_task(self, task: ExecutionTask, *, verbose: bool = False) -> str:
        lines = [
            f"task_key: {task.task_key}",
            f"subject: {task.subject}",
            f"state: {self._display_state(task)}",
        ]
        if task.depends_on:
            lines.append(f"depends on: {', '.join(task.depends_on)}")
        if task.blocked_by:
            lines.append(f"waiting for: {', '.join(task.blocked_by)}")
        if verbose or task.description:
            lines.append(f"description: {task.description}")
        if verbose or task.notes:
            lines.append(f"notes: {task.notes}")
        return "\n".join(lines)

    def _display_state(self, task: ExecutionTask) -> str:
        """Translate internal task status into a user-facing state phrase."""
        if task.blocked_by:
            return f"waiting for: {', '.join(task.blocked_by)}"
        if task.status == TaskStatus.IN_PROGRESS:
            return "in progress"
        if task.status == TaskStatus.COMPLETED:
            return "completed"
        if task.status == TaskStatus.CANCELLED:
            return "cancelled"
        return "ready" if task.ready else "pending"

    def _bounded(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + "\n... truncated ..."
