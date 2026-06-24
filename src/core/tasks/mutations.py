"""SQLite mutation helpers for Execution-scoped private tasks."""

from uuid import uuid4

from src.core.tasks.models import TaskPlanItem, TaskStatus


class TaskMutationStore:
    """Persist task rows and dependency edges inside caller-owned transactions."""

    def upsert_plan_items(
        self,
        conn,
        execution_id: str,
        items: list[TaskPlanItem],
        existing_by_key: dict,
    ) -> None:
        """Insert new task rows or update existing planning metadata."""
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

    def replace_dependencies(
        self,
        conn,
        execution_id: str,
        task_id: str,
        dependency_keys: tuple[str, ...] | list[str],
        key_to_id: dict[str, str],
    ) -> None:
        """Replace all dependency edges for one task."""
        conn.execute(
            "DELETE FROM execution_task_dependencies WHERE task_id=?",
            (task_id,),
        )
        for dependency_key in dependency_keys:
            conn.execute(
                """
                INSERT INTO execution_task_dependencies(
                    execution_id, task_id, depends_on_task_id
                ) VALUES (?, ?, ?)
                """,
                (execution_id, task_id, key_to_id[dependency_key]),
            )

    def update_task(
        self,
        conn,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        subject: str | None = None,
        description: str | None = None,
        notes: str | None = None,
    ) -> None:
        """Update mutable task fields without exposing SQL assembly upstream."""
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
        if status is not None:
            assignments.append("status=?")
            params.append(status.value)
            if status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
                assignments.append("completed_at=CURRENT_TIMESTAMP")
            else:
                assignments.append("completed_at=NULL")
        params.append(task_id)
        conn.execute(
            f"UPDATE execution_tasks SET {', '.join(assignments)} WHERE task_id=?",
            tuple(params),
        )
