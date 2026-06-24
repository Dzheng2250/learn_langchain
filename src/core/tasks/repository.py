"""SQLite repository for Execution-scoped private tasks."""

from __future__ import annotations

from src.config.tasks import TaskSettings
from src.core.state.database import LocalStateDatabase
from src.core.tasks.context import ToolExecutionContext
from src.core.tasks.execution_context import TaskExecutionContextGuard
from src.core.tasks.models import ExecutionTask, TaskPlanItem, TaskStatus
from src.core.tasks.mutations import TaskMutationStore
from src.core.tasks.queries import TaskQueryStore
from src.core.tasks.validation import TaskPlanValidator


class TaskRepository:
    """Persist and validate task plans for one active Execution."""

    def __init__(
        self,
        database: LocalStateDatabase,
        settings: TaskSettings | None = None,
    ) -> None:
        self.database = database
        self.settings = settings or TaskSettings.load()
        self.queries = TaskQueryStore()
        self.mutations = TaskMutationStore()
        self.context_guard = TaskExecutionContextGuard()
        self.validator = TaskPlanValidator(self.settings)

    def plan(
        self,
        context: ToolExecutionContext,
        items: list[TaskPlanItem],
    ) -> list[ExecutionTask]:
        """Upsert a batch of tasks and replace dependencies for those tasks."""
        execution_id = context.require_execution_id()
        if not items:
            raise ValueError("task_plan requires at least one task")
        self.validator.validate_plan_items(items)

        with self.database.transaction() as conn:
            self.context_guard.assert_matches(conn, context)
            existing = self.queries.load_task_rows(conn, execution_id)
            existing_keys = {row["task_key"] for row in existing}
            planned_keys = {item.task_key for item in items}
            all_keys = existing_keys | planned_keys
            if len(all_keys) > self.settings.max_tasks_per_execution:
                raise ValueError(
                    "task plan exceeds max tasks per execution "
                    f"({self.settings.max_tasks_per_execution})"
                )

            edges = self.queries.load_dependency_edges(conn, execution_id)
            for item in items:
                missing = set(item.depends_on) - all_keys
                if missing:
                    raise ValueError(f"unknown dependency for {item.task_key}: {sorted(missing)}")
                edges[item.task_key] = set(item.depends_on)
            self.validator.assert_acyclic(edges)

            existing_by_key = {row["task_key"]: row for row in existing}
            self.mutations.upsert_plan_items(conn, execution_id, items, existing_by_key)

            key_to_id = self.queries.key_to_id(conn, execution_id)
            for item in items:
                self.mutations.replace_dependencies(
                    conn,
                    execution_id,
                    key_to_id[item.task_key],
                    item.depends_on,
                    key_to_id,
                )

            # After rewriting dependencies, validate that existing tasks
            # whose status is in_progress or completed do not now depend on
            # unfinished tasks.  The plan() method preserves the existing
            # row status for tasks that already exist, so an already-completed
            # task could be rewired to a pending dependency without this check.
            for item in items:
                existing_row = existing_by_key.get(item.task_key)
                if existing_row is None:
                    continue
                current_status = TaskStatus(existing_row["status"])
                if current_status in {TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED}:
                    task_id = key_to_id[item.task_key]
                    blockers = self.queries.blocking_dependencies(conn, execution_id, task_id)
                    if blockers:
                        raise ValueError(
                            f"task {item.task_key} is blocked by unfinished "
                            f"dependencies: {blockers}"
                        )

        return self.list(context)

    def update(
        self,
        context: ToolExecutionContext,
        task_key: str,
        *,
        status: TaskStatus | str | None = None,
        subject: str | None = None,
        description: str | None = None,
        notes: str | None = None,
        depends_on: list[str] | tuple[str, ...] | None = None,
    ) -> ExecutionTask:
        """Update one task and optionally replace its dependency list."""
        execution_id = context.require_execution_id()
        self.validator.validate_task_key(task_key)
        next_status = TaskStatus(status) if status is not None else None
        subject = self.validator.limit_optional(subject, self.settings.subject_max_chars, "subject")
        description = self.validator.limit_optional(
            description,
            self.settings.description_max_chars,
            "description",
        )
        notes = self.validator.limit_optional(notes, self.settings.notes_max_chars, "notes")

        with self.database.transaction() as conn:
            self.context_guard.assert_matches(conn, context)
            row = self.queries.row_by_key(conn, execution_id, task_key)
            if row is None:
                raise ValueError(f"unknown task_key: {task_key}")
            key_to_id = self.queries.key_to_id(conn, execution_id)
            edges = self.queries.load_dependency_edges(conn, execution_id)
            if depends_on is not None:
                clean_dependencies = tuple(
                    dict.fromkeys(dep.strip() for dep in depends_on if dep.strip())
                )
                self.validator.validate_dependency_keys(task_key, clean_dependencies, set(key_to_id))
                edges[task_key] = set(clean_dependencies)
                self.validator.assert_acyclic(edges)
                self.mutations.replace_dependencies(
                    conn,
                    execution_id,
                    row["task_id"],
                    clean_dependencies,
                    key_to_id,
                )

                # After rewriting dependencies, validate that the effective
                # status remains consistent with the new dependency graph.
                # An in_progress or completed task must not depend on tasks
                # that are still unfinished.
                effective_status = next_status if next_status is not None else TaskStatus(row["status"])
                if effective_status in {TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED}:
                    blockers = self.queries.blocking_dependencies(conn, execution_id, row["task_id"])
                    if blockers:
                        raise ValueError(
                            f"task {task_key} is blocked by unfinished dependencies: {blockers}"
                        )

            if next_status in {TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED}:
                blockers = self.queries.blocking_dependencies(conn, execution_id, row["task_id"])
                if blockers:
                    raise ValueError(
                        f"task {task_key} is blocked by unfinished dependencies: {blockers}"
                    )

            self.mutations.update_task(
                conn,
                row["task_id"],
                status=next_status,
                subject=subject,
                description=description,
                notes=notes,
            )

        task = self.get(context, task_key)
        if task is None:
            raise RuntimeError("updated task disappeared")
        return task

    def list(self, context: ToolExecutionContext) -> list[ExecutionTask]:
        """Return all tasks for the active Execution in deterministic order."""
        execution_id = context.require_execution_id()
        with self.database.connect() as conn:
            self.context_guard.assert_matches(conn, context)
            return self.queries.load_tasks(conn, execution_id)

    def get(self, context: ToolExecutionContext, task_key: str) -> ExecutionTask | None:
        """Return one task by semantic key inside the active Execution."""
        execution_id = context.require_execution_id()
        self.validator.validate_task_key(task_key)
        with self.database.connect() as conn:
            self.context_guard.assert_matches(conn, context)
            tasks = self.queries.load_tasks(conn, execution_id)
        return next((task for task in tasks if task.task_key == task_key), None)
