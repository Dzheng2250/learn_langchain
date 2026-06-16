"""SQLite repository for Execution-scoped private tasks."""

from __future__ import annotations

import re
from collections import defaultdict
from uuid import uuid4

from src.config.tasks import TaskSettings
from src.core.state.database import LocalStateDatabase
from src.core.tasks.context import ToolExecutionContext
from src.core.tasks.models import ExecutionTask, TaskPlanItem, TaskStatus


_TASK_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class TaskRepository:
    """Persist and validate task plans for one active Execution."""

    def __init__(
        self,
        database: LocalStateDatabase,
        settings: TaskSettings | None = None,
    ) -> None:
        self.database = database
        self.settings = settings or TaskSettings.load()

    def plan(
        self,
        context: ToolExecutionContext,
        items: list[TaskPlanItem],
    ) -> list[ExecutionTask]:
        """Upsert a batch of tasks and replace dependencies for those tasks."""
        execution_id = context.require_execution_id()
        if not items:
            raise ValueError("task_plan requires at least one task")
        self._validate_unique_keys(items)
        for item in items:
            self._validate_plan_item(item)

        with self.database.transaction() as conn:
            self._assert_execution_context(conn, context)
            existing = self._load_task_rows(conn, execution_id)
            existing_keys = {row["task_key"] for row in existing}
            planned_keys = {item.task_key for item in items}
            all_keys = existing_keys | planned_keys
            if len(all_keys) > self.settings.max_tasks_per_execution:
                raise ValueError(
                    "task plan exceeds max tasks per execution "
                    f"({self.settings.max_tasks_per_execution})"
                )

            edges = self._load_dependency_edges(conn, execution_id)
            for item in items:
                missing = set(item.depends_on) - all_keys
                if missing:
                    raise ValueError(f"unknown dependency for {item.task_key}: {sorted(missing)}")
                edges[item.task_key] = set(item.depends_on)
            self._assert_acyclic(edges)

            existing_by_key = {row["task_key"]: row for row in existing}
            for item in items:
                row = existing_by_key.get(item.task_key)
                if row is None:
                    conn.execute(
                        """
                        INSERT INTO execution_tasks(
                            task_id, execution_id, task_key, subject,
                            description, notes, status, ordinal
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            uuid4().hex,
                            execution_id,
                            item.task_key,
                            item.subject,
                            item.description,
                            item.notes,
                            TaskStatus.PENDING,
                            item.ordinal,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE execution_tasks
                        SET subject=?, description=?, notes=?, ordinal=?,
                            version=version+1, updated_at=CURRENT_TIMESTAMP
                        WHERE task_id=?
                        """,
                        (
                            item.subject,
                            item.description,
                            item.notes,
                            item.ordinal,
                            row["task_id"],
                        ),
                    )

            key_to_id = self._key_to_id(conn, execution_id)
            for item in items:
                task_id = key_to_id[item.task_key]
                conn.execute(
                    "DELETE FROM execution_task_dependencies WHERE task_id=?",
                    (task_id,),
                )
                for dependency_key in item.depends_on:
                    conn.execute(
                        """
                        INSERT INTO execution_task_dependencies(
                            execution_id, task_id, depends_on_task_id
                        ) VALUES (?, ?, ?)
                        """,
                        (execution_id, task_id, key_to_id[dependency_key]),
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
        self._validate_task_key(task_key)
        next_status = TaskStatus(status) if status is not None else None
        subject = self._limit_optional(subject, self.settings.subject_max_chars, "subject")
        description = self._limit_optional(
            description,
            self.settings.description_max_chars,
            "description",
        )
        notes = self._limit_optional(notes, self.settings.notes_max_chars, "notes")

        with self.database.transaction() as conn:
            self._assert_execution_context(conn, context)
            row = self._row_by_key(conn, execution_id, task_key)
            if row is None:
                raise ValueError(f"unknown task_key: {task_key}")
            key_to_id = self._key_to_id(conn, execution_id)
            edges = self._load_dependency_edges(conn, execution_id)
            if depends_on is not None:
                clean_dependencies = tuple(
                    dict.fromkeys(dep.strip() for dep in depends_on if dep.strip())
                )
                self._validate_dependency_keys(task_key, clean_dependencies, set(key_to_id))
                edges[task_key] = set(clean_dependencies)
                self._assert_acyclic(edges)
                conn.execute(
                    "DELETE FROM execution_task_dependencies WHERE task_id=?",
                    (row["task_id"],),
                )
                for dependency_key in clean_dependencies:
                    conn.execute(
                        """
                        INSERT INTO execution_task_dependencies(
                            execution_id, task_id, depends_on_task_id
                        ) VALUES (?, ?, ?)
                        """,
                        (execution_id, row["task_id"], key_to_id[dependency_key]),
                    )

            if next_status in {TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED}:
                blockers = self._blocking_dependencies(conn, execution_id, row["task_id"])
                if blockers:
                    raise ValueError(
                        f"task {task_key} is blocked by unfinished dependencies: {blockers}"
                    )

            assignments = ["version=version+1", "updated_at=CURRENT_TIMESTAMP"]
            params: list[object] = []
            if subject is not None:
                assignments.append("subject=?")
                params.append(subject)
            if description is not None:
                assignments.append("description=?")
                params.append(description)
            if notes is not None:
                assignments.append("notes=?")
                params.append(notes)
            if next_status is not None:
                assignments.append("status=?")
                params.append(next_status.value)
                if next_status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
                    assignments.append("completed_at=CURRENT_TIMESTAMP")
                else:
                    assignments.append("completed_at=NULL")
            params.append(row["task_id"])
            conn.execute(
                f"UPDATE execution_tasks SET {', '.join(assignments)} WHERE task_id=?",
                tuple(params),
            )

        task = self.get(context, task_key)
        if task is None:
            raise RuntimeError("updated task disappeared")
        return task

    def list(self, context: ToolExecutionContext) -> list[ExecutionTask]:
        """Return all tasks for the active Execution in deterministic order."""
        execution_id = context.require_execution_id()
        with self.database.connect() as conn:
            self._assert_execution_context(conn, context)
            return self._load_tasks(conn, execution_id)

    def get(self, context: ToolExecutionContext, task_key: str) -> ExecutionTask | None:
        """Return one task by semantic key inside the active Execution."""
        execution_id = context.require_execution_id()
        self._validate_task_key(task_key)
        with self.database.connect() as conn:
            self._assert_execution_context(conn, context)
            tasks = self._load_tasks(conn, execution_id)
        return next((task for task in tasks if task.task_key == task_key), None)

    def _assert_execution_context(self, conn, context: ToolExecutionContext) -> None:
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

    def _load_tasks(self, conn, execution_id: str) -> list[ExecutionTask]:
        rows = self._load_task_rows(conn, execution_id)
        depends_by_task = self._dependency_keys_by_task(conn, execution_id)
        blocked_by_task = self._blocking_keys_by_task(conn, execution_id)
        return [
            ExecutionTask(
                task_id=row["task_id"],
                execution_id=row["execution_id"],
                task_key=row["task_key"],
                subject=row["subject"],
                description=row["description"],
                status=TaskStatus(row["status"]),
                notes=row["notes"],
                ordinal=int(row["ordinal"]),
                version=int(row["version"]),
                depends_on=tuple(depends_by_task[row["task_key"]]),
                blocked_by=tuple(blocked_by_task[row["task_key"]]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
            )
            for row in rows
        ]

    def _load_task_rows(self, conn, execution_id: str):
        return conn.execute(
            """
            SELECT * FROM execution_tasks
            WHERE execution_id=?
            ORDER BY ordinal, created_at, task_key
            """,
            (execution_id,),
        ).fetchall()

    def _row_by_key(self, conn, execution_id: str, task_key: str):
        return conn.execute(
            "SELECT * FROM execution_tasks WHERE execution_id=? AND task_key=?",
            (execution_id, task_key),
        ).fetchone()

    def _key_to_id(self, conn, execution_id: str) -> dict[str, str]:
        rows = conn.execute(
            "SELECT task_key, task_id FROM execution_tasks WHERE execution_id=?",
            (execution_id,),
        ).fetchall()
        return {row["task_key"]: row["task_id"] for row in rows}

    def _load_dependency_edges(self, conn, execution_id: str) -> dict[str, set[str]]:
        edges: dict[str, set[str]] = defaultdict(set)
        for row in self._load_task_rows(conn, execution_id):
            edges[row["task_key"]]
        rows = conn.execute(
            """
            SELECT t.task_key AS task_key, d.task_key AS dependency_key
            FROM execution_task_dependencies dep
            JOIN execution_tasks t ON t.task_id = dep.task_id
            JOIN execution_tasks d ON d.task_id = dep.depends_on_task_id
            WHERE dep.execution_id=?
            """,
            (execution_id,),
        ).fetchall()
        for row in rows:
            edges[row["task_key"]].add(row["dependency_key"])
        return edges

    def _dependency_keys_by_task(self, conn, execution_id: str) -> dict[str, list[str]]:
        result: dict[str, list[str]] = defaultdict(list)
        rows = conn.execute(
            """
            SELECT t.task_key AS task_key, d.task_key AS dependency_key
            FROM execution_task_dependencies dep
            JOIN execution_tasks t ON t.task_id = dep.task_id
            JOIN execution_tasks d ON d.task_id = dep.depends_on_task_id
            WHERE dep.execution_id=?
            ORDER BY d.ordinal, d.task_key
            """,
            (execution_id,),
        ).fetchall()
        for row in rows:
            result[row["task_key"]].append(row["dependency_key"])
        return result

    def _blocking_keys_by_task(self, conn, execution_id: str) -> dict[str, list[str]]:
        result: dict[str, list[str]] = defaultdict(list)
        rows = conn.execute(
            """
            SELECT t.task_key AS task_key, d.task_key AS dependency_key
            FROM execution_task_dependencies dep
            JOIN execution_tasks t ON t.task_id = dep.task_id
            JOIN execution_tasks d ON d.task_id = dep.depends_on_task_id
            WHERE dep.execution_id=? AND d.status IN (?, ?)
            ORDER BY d.ordinal, d.task_key
            """,
            (execution_id, TaskStatus.PENDING, TaskStatus.IN_PROGRESS),
        ).fetchall()
        for row in rows:
            result[row["task_key"]].append(row["dependency_key"])
        return result

    def _blocking_dependencies(self, conn, execution_id: str, task_id: str) -> list[str]:
        rows = conn.execute(
            """
            SELECT d.task_key
            FROM execution_task_dependencies dep
            JOIN execution_tasks d ON d.task_id = dep.depends_on_task_id
            WHERE dep.execution_id=? AND dep.task_id=? AND d.status IN (?, ?)
            ORDER BY d.ordinal, d.task_key
            """,
            (execution_id, task_id, TaskStatus.PENDING, TaskStatus.IN_PROGRESS),
        ).fetchall()
        return [row["task_key"] for row in rows]

    def _validate_plan_item(self, item: TaskPlanItem) -> None:
        self._validate_task_key(item.task_key)
        self._limit_required(item.subject, self.settings.subject_max_chars, "subject")
        self._limit_required(
            item.description,
            self.settings.description_max_chars,
            "description",
        )
        self._limit_required(item.notes, self.settings.notes_max_chars, "notes")
        self._validate_dependency_keys(item.task_key, item.depends_on, set(item.depends_on) | {item.task_key})

    def _validate_unique_keys(self, items: list[TaskPlanItem]) -> None:
        keys = [item.task_key for item in items]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(f"duplicate task_key values: {duplicates}")

    def _validate_task_key(self, task_key: str) -> None:
        if not task_key or len(task_key) > self.settings.task_key_max_chars:
            raise ValueError(
                f"task_key must be 1-{self.settings.task_key_max_chars} characters"
            )
        if not _TASK_KEY_RE.match(task_key):
            raise ValueError(
                "task_key must start with a lowercase letter and contain only "
                "lowercase letters, digits, underscores, or hyphens"
            )

    def _validate_dependency_keys(
        self,
        task_key: str,
        depends_on: tuple[str, ...] | list[str],
        available_keys: set[str],
    ) -> None:
        for dependency_key in depends_on:
            self._validate_task_key(dependency_key)
            if dependency_key == task_key:
                raise ValueError("task cannot depend on itself")
            if dependency_key not in available_keys:
                raise ValueError(f"unknown dependency for {task_key}: {dependency_key}")

    def _assert_acyclic(self, edges: dict[str, set[str]]) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str, path: tuple[str, ...]) -> None:
            if node in visiting:
                raise ValueError("task dependency cycle detected: " + " -> ".join((*path, node)))
            if node in visited:
                return
            visiting.add(node)
            for dependency in edges.get(node, set()):
                visit(dependency, (*path, node))
            visiting.remove(node)
            visited.add(node)

        for key in tuple(edges):
            visit(key, ())

    def _limit_required(self, value: str, limit: int, field_name: str) -> str:
        if len(value) > limit:
            raise ValueError(f"{field_name} exceeds {limit} characters")
        return value

    def _limit_optional(self, value: str | None, limit: int, field_name: str) -> str | None:
        if value is None:
            return None
        return self._limit_required(value, limit, field_name)
