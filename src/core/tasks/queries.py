"""Query helpers for Execution-scoped private tasks."""

from collections import defaultdict

from src.core.tasks.models import ExecutionTask, TaskStatus


class TaskQueryStore:
    """Load task rows, dependency graphs, and domain task views."""

    def load_tasks(self, conn, execution_id: str) -> list[ExecutionTask]:
        rows = self.load_task_rows(conn, execution_id)
        depends_by_task = self.dependency_keys_by_task(conn, execution_id)
        blocked_by_task = self.blocking_keys_by_task(conn, execution_id)
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

    def load_task_rows(self, conn, execution_id: str):
        return conn.execute(
            """
            SELECT * FROM execution_tasks
            WHERE execution_id=?
            ORDER BY ordinal, created_at, task_key
            """,
            (execution_id,),
        ).fetchall()

    def row_by_key(self, conn, execution_id: str, task_key: str):
        return conn.execute(
            "SELECT * FROM execution_tasks WHERE execution_id=? AND task_key=?",
            (execution_id, task_key),
        ).fetchone()

    def key_to_id(self, conn, execution_id: str) -> dict[str, str]:
        rows = conn.execute(
            "SELECT task_key, task_id FROM execution_tasks WHERE execution_id=?",
            (execution_id,),
        ).fetchall()
        return {row["task_key"]: row["task_id"] for row in rows}

    def load_dependency_edges(self, conn, execution_id: str) -> dict[str, set[str]]:
        edges: dict[str, set[str]] = defaultdict(set)
        for row in self.load_task_rows(conn, execution_id):
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

    def dependency_keys_by_task(self, conn, execution_id: str) -> dict[str, list[str]]:
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

    def blocking_keys_by_task(self, conn, execution_id: str) -> dict[str, list[str]]:
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

    def blocking_dependencies(self, conn, execution_id: str, task_id: str) -> list[str]:
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
