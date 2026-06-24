"""SQLite row mappers for maintenance jobs and status diagnostics."""

import json

from src.core.maintenance.models import MaintenanceJob
from src.core.maintenance.types import MaintenanceStatus


def maintenance_job_from_row(row) -> MaintenanceJob:
    """Convert a SQLite row into a domain maintenance job model."""
    return MaintenanceJob(
        job_id=row["job_id"],
        workspace_id=row["workspace_id"],
        session_id=row["session_id"],
        execution_id=row["execution_id"],
        job_type=row["job_type"],
        dedupe_key=row["dedupe_key"],
        priority=int(row["priority"]),
        status=MaintenanceStatus(row["status"]),
        payload=json.loads(row["payload"] or "{}"),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
    )


def maintenance_failure_from_row(row) -> dict:
    """Convert a terminal failed job row into user/operator-facing status data."""
    return {
        "job_id": row["job_id"],
        "execution_id": row["execution_id"],
        "job_type": row["job_type"],
        "attempts": int(row["attempts"]),
        "max_attempts": int(row["max_attempts"]),
        "last_error": row["last_error"],
        "updated_at": row["updated_at"],
        "finished_at": row["finished_at"],
    }
