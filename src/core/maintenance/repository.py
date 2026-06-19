"""Transactional outbox repository for durable background maintenance."""

import json
from uuid import uuid4

from src.config.maintenance import MaintenanceSettings
from src.core.maintenance.models import MaintenanceJob, MaintenanceJobSpec
from src.core.maintenance.inspection import MaintenanceInspectionStore
from src.core.maintenance.records import maintenance_job_from_row
from src.core.maintenance.types import MaintenanceStatus
from src.core.state.database import LocalStateDatabase


class MaintenanceRepository:
    """Enqueue, lease, retry, and inspect maintenance jobs in ``state.db``."""

    def __init__(
        self,
        database: LocalStateDatabase,
        settings: MaintenanceSettings | None = None,
    ) -> None:
        self.database = database
        self.settings = settings or MaintenanceSettings.load()
        self.settings.validate()
        self.inspection = MaintenanceInspectionStore(database)

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
                max(1, int(spec.max_attempts or self.settings.default_max_attempts)),
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

    def claim_next(self, *, lease_seconds: float | None = None) -> MaintenanceJob | None:
        """Atomically lease the highest-priority ready job."""
        lease_seconds = (
            self.settings.lease_seconds if lease_seconds is None else lease_seconds
        )
        modifier = f"+{max(1, int(lease_seconds))} seconds"
        with self.database.transaction() as conn:
            conn.execute(
                """
                UPDATE maintenance_jobs
                SET status=?, lease_expires_at=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE status=? AND lease_expires_at <= CURRENT_TIMESTAMP
                """,
                (MaintenanceStatus.PENDING, MaintenanceStatus.RUNNING),
            )
            row = conn.execute(
                """
                SELECT * FROM maintenance_jobs
                WHERE status=? AND next_attempt_at <= CURRENT_TIMESTAMP
                ORDER BY priority DESC, created_at, job_id
                LIMIT 1
                """,
                (MaintenanceStatus.PENDING,),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                """
                UPDATE maintenance_jobs
                SET status=?, attempts=attempts + 1,
                    lease_expires_at=datetime('now', ?), updated_at=CURRENT_TIMESTAMP
                WHERE job_id=? AND status=?
                """,
                (
                    MaintenanceStatus.RUNNING,
                    modifier,
                    row["job_id"],
                    MaintenanceStatus.PENDING,
                ),
            )
            claimed = conn.execute(
                "SELECT * FROM maintenance_jobs WHERE job_id=?",
                (row["job_id"],),
            ).fetchone()
        return maintenance_job_from_row(claimed)

    def succeed(self, job_id: str) -> None:
        """Mark one task complete."""
        with self.database.transaction() as conn:
            conn.execute(
                """
                UPDATE maintenance_jobs
                SET status=?, lease_expires_at=NULL, finished_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP, last_error=''
                WHERE job_id=?
                """,
                (MaintenanceStatus.SUCCEEDED, job_id),
            )

    def fail(self, job: MaintenanceJob, error: str) -> None:
        """Retry a failed task with bounded exponential backoff."""
        terminal = job.attempts >= job.max_attempts
        delay_seconds = min(
            self.settings.max_retry_delay_seconds,
            2 ** max(0, job.attempts - 1),
        )
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
                    MaintenanceStatus.FAILED if terminal else MaintenanceStatus.PENDING,
                    error[: self.settings.error_preview_limit],
                    f"+{delay_seconds} seconds",
                    1 if terminal else 0,
                    job.job_id,
                ),
            )

    def counts_for_session(self, workspace_id: str, session_id: str) -> dict[str, int]:
        """Return pending/running/failed counts for a Session control response."""
        return self.inspection.counts_for_session(workspace_id, session_id)

    def recent_failures_for_session(
        self,
        workspace_id: str,
        session_id: str,
        *,
        limit: int = 3,
    ) -> list[dict]:
        """Return recent terminal maintenance failures for operator-facing status.

        Background tasks such as context summaries and memory extraction may call
        the LLM outside the current foreground chat request. Exposing their
        failed job type lets clients distinguish those failures from the active
        Agent turn.
        """
        return self.inspection.recent_failures_for_session(
            workspace_id,
            session_id,
            limit=limit,
        )

    def get_by_dedupe_key(self, dedupe_key: str) -> MaintenanceJob | None:
        """Return one task for tests and diagnostics."""
        return self.inspection.get_by_dedupe_key(dedupe_key)

    def requeue_failed(self, dedupe_key: str) -> bool:
        """Allow recovery coordination to retry a terminal checkpoint cleanup."""
        with self.database.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE maintenance_jobs
                SET status=?, attempts=0, next_attempt_at=CURRENT_TIMESTAMP,
                    lease_expires_at=NULL, finished_at=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE dedupe_key=? AND status=?
                """,
                (MaintenanceStatus.PENDING, dedupe_key, MaintenanceStatus.FAILED),
            )
        return cur.rowcount == 1
