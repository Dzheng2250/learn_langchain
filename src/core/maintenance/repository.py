"""Transactional outbox repository for durable background maintenance."""

import json
from uuid import uuid4

from src.core.maintenance.models import MaintenanceJob, MaintenanceJobSpec
from src.core.state.database import LocalStateDatabase


class MaintenanceRepository:
    """Enqueue, lease, retry, and inspect maintenance jobs in ``state.db``."""

    def __init__(self, database: LocalStateDatabase) -> None:
        self.database = database

    def enqueue_in_transaction(self, conn, spec: MaintenanceJobSpec) -> str:
        """Insert one idempotent task inside an existing business transaction."""
        job_id = str(uuid4())
        conn.execute(
            """
            INSERT INTO maintenance_jobs(
                job_id, workspace_id, session_id, execution_id, job_type,
                dedupe_key, priority, payload, max_attempts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dedupe_key) DO NOTHING
            """,
            (
                job_id,
                spec.workspace_id,
                spec.session_id,
                spec.execution_id,
                spec.job_type,
                spec.dedupe_key,
                int(spec.priority),
                json.dumps(spec.payload, ensure_ascii=False, default=str),
                max(1, int(spec.max_attempts)),
            ),
        )
        row = conn.execute(
            "SELECT job_id FROM maintenance_jobs WHERE dedupe_key = ?",
            (spec.dedupe_key,),
        ).fetchone()
        return row["job_id"]

    def enqueue(self, spec: MaintenanceJobSpec) -> str:
        """Insert one idempotent task in its own short transaction."""
        with self.database.transaction() as conn:
            return self.enqueue_in_transaction(conn, spec)

    def claim_next(self, *, lease_seconds: float = 60) -> MaintenanceJob | None:
        """Atomically lease the highest-priority ready job."""
        modifier = f"+{max(1, int(lease_seconds))} seconds"
        with self.database.transaction() as conn:
            conn.execute(
                """
                UPDATE maintenance_jobs
                SET status='pending', lease_expires_at=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE status='running' AND lease_expires_at <= CURRENT_TIMESTAMP
                """
            )
            row = conn.execute(
                """
                SELECT * FROM maintenance_jobs
                WHERE status='pending' AND next_attempt_at <= CURRENT_TIMESTAMP
                ORDER BY priority DESC, created_at, job_id
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            conn.execute(
                """
                UPDATE maintenance_jobs
                SET status='running', attempts=attempts + 1,
                    lease_expires_at=datetime('now', ?), updated_at=CURRENT_TIMESTAMP
                WHERE job_id=? AND status='pending'
                """,
                (modifier, row["job_id"]),
            )
            claimed = conn.execute(
                "SELECT * FROM maintenance_jobs WHERE job_id=?",
                (row["job_id"],),
            ).fetchone()
        return self._from_row(claimed)

    def succeed(self, job_id: str) -> None:
        """Mark one task complete."""
        with self.database.transaction() as conn:
            conn.execute(
                """
                UPDATE maintenance_jobs
                SET status='succeeded', lease_expires_at=NULL, finished_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP, last_error=''
                WHERE job_id=?
                """,
                (job_id,),
            )

    def fail(self, job: MaintenanceJob, error: str) -> None:
        """Retry a failed task with bounded exponential backoff."""
        terminal = job.attempts >= job.max_attempts
        delay_seconds = min(300, 2 ** max(0, job.attempts - 1))
        with self.database.transaction() as conn:
            conn.execute(
                """
                UPDATE maintenance_jobs
                SET status=?, lease_expires_at=NULL, last_error=?,
                    next_attempt_at=datetime('now', ?), updated_at=CURRENT_TIMESTAMP,
                    finished_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END
                WHERE job_id=?
                """,
                (
                    "failed" if terminal else "pending",
                    error[:2000],
                    f"+{delay_seconds} seconds",
                    1 if terminal else 0,
                    job.job_id,
                ),
            )

    def counts_for_session(self, workspace_id: str, session_id: str) -> dict[str, int]:
        """Return pending/running/failed counts for a Session control response."""
        counts = {"pending": 0, "running": 0, "failed": 0}
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT status, count(*) AS count FROM maintenance_jobs
                WHERE workspace_id=? AND session_id=?
                  AND status IN ('pending', 'running', 'failed')
                GROUP BY status
                """,
                (workspace_id, session_id),
            ).fetchall()
        for row in rows:
            counts[row["status"]] = int(row["count"])
        return counts

    def get_by_dedupe_key(self, dedupe_key: str) -> MaintenanceJob | None:
        """Return one task for tests and diagnostics."""
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM maintenance_jobs WHERE dedupe_key=?",
                (dedupe_key,),
            ).fetchone()
        return self._from_row(row) if row else None

    def requeue_failed(self, dedupe_key: str) -> bool:
        """Allow recovery coordination to retry a terminal checkpoint cleanup."""
        with self.database.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE maintenance_jobs
                SET status='pending', attempts=0, next_attempt_at=CURRENT_TIMESTAMP,
                    lease_expires_at=NULL, finished_at=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE dedupe_key=? AND status='failed'
                """,
                (dedupe_key,),
            )
        return cur.rowcount == 1

    @staticmethod
    def _from_row(row) -> MaintenanceJob:
        return MaintenanceJob(
            job_id=row["job_id"],
            workspace_id=row["workspace_id"],
            session_id=row["session_id"],
            execution_id=row["execution_id"],
            job_type=row["job_type"],
            dedupe_key=row["dedupe_key"],
            priority=int(row["priority"]),
            status=row["status"],
            payload=json.loads(row["payload"] or "{}"),
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
        )
