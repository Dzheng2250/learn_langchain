"""Stable models for durable background maintenance work."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MaintenanceJobSpec:
    """One task inserted atomically with the business change that requires it."""

    job_type: str
    dedupe_key: str
    workspace_id: str
    session_id: str
    payload: dict = field(default_factory=dict)
    execution_id: str | None = None
    priority: int = 0
    max_attempts: int = 5


@dataclass(frozen=True)
class MaintenanceJob:
    """One claimed task with retry and lease metadata."""

    job_id: str
    workspace_id: str
    session_id: str
    execution_id: str | None
    job_type: str
    dedupe_key: str
    priority: int
    status: str
    payload: dict
    attempts: int
    max_attempts: int
