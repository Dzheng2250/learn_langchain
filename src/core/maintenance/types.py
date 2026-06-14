"""Stable domain vocabulary for durable maintenance work."""

from enum import IntEnum, StrEnum


class MaintenanceJobType(StrEnum):
    """Supported work that may run after a Turn response is released."""

    CONTEXT_SUMMARY = "context_summary"
    MEMORY_EXTRACT = "memory_extract"
    CHECKPOINT_CLEANUP = "checkpoint_cleanup"


class MaintenanceStatus(StrEnum):
    """Lifecycle states persisted for one maintenance job."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class MaintenancePriority(IntEnum):
    """Relative ordering for built-in maintenance work."""

    NORMAL_MEMORY = 10
    CONTEXT_SUMMARY = 20
    EXPLICIT_MEMORY = 30
    CHECKPOINT_CLEANUP = 100
