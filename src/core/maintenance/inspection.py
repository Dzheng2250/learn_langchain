"""Read-only inspection queries for maintenance jobs."""

from src.core.maintenance.models import MaintenanceJob
from src.core.maintenance.records import (
    maintenance_failure_from_row,
    maintenance_job_from_row,
)
from src.core.maintenance.types import MaintenanceStatus
from src.core.state.database import LocalStateDatabase


class MaintenanceInspectionStore:
    """Expose maintenance job status without owning scheduler mutations."""

    def __init__(self, database: LocalStateDatabase) -> None:
        self.database = database

    def counts_for_session(self, workspace_id: str, session_id: str) -> dict[str, int]:
        """Return pending/running/failed counts for a Session control response."""
        counts = {
            MaintenanceStatus.PENDING.value: 0,
            MaintenanceStatus.RUNNING.value: 0,
            MaintenanceStatus.FAILED.value: 0,
        }
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT status, count(*) AS count FROM maintenance_jobs
                WHERE workspace_id=? AND session_id=?
                  AND status IN (?, ?, ?)
                GROUP BY status
                """,
                (
                    workspace_id,
                    session_id,
                    MaintenanceStatus.PENDING,
                    MaintenanceStatus.RUNNING,
                    MaintenanceStatus.FAILED,
                ),
            ).fetchall()
        for row in rows:
            counts[row["status"]] = int(row["count"])
        return counts

    def recent_failures_for_session(
        self,
        workspace_id: str,
        session_id: str,
        *,
        limit: int = 3,
    ) -> list[dict]:
        """Return recent terminal failures for operator-facing status."""
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT job_id, execution_id, job_type, attempts, max_attempts,
                       last_error, updated_at, finished_at
                FROM maintenance_jobs
                WHERE workspace_id=? AND session_id=? AND status=?
                ORDER BY updated_at DESC, job_id DESC
                LIMIT ?
                """,
                (
                    workspace_id,
                    session_id,
                    MaintenanceStatus.FAILED,
                    max(0, int(limit)),
                ),
            ).fetchall()
        return [maintenance_failure_from_row(row) for row in rows]

    def get_by_dedupe_key(self, dedupe_key: str) -> MaintenanceJob | None:
        """Return one task for tests and diagnostics."""
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM maintenance_jobs WHERE dedupe_key=?",
                (dedupe_key,),
            ).fetchone()
        return maintenance_job_from_row(row) if row else None
