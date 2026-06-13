"""Stable domain vocabulary for execution and checkpoint persistence."""

from enum import StrEnum


class ExecutionStatus(StrEnum):
    """Lifecycle states persisted for a recoverable Agent execution."""

    RUNNING = "running"
    PAUSED_BUDGET = "paused_budget"
    PAUSED_ERROR = "paused_error"
    PAUSED_CONFIRMATION = "paused_confirmation"
    PAUSED_RECOVERY = "paused_recovery"
    UNRECOVERABLE_CHECKPOINT = "unrecoverable_checkpoint"
    COMPLETED = "completed"
    DISCARDED = "discarded"

    @classmethod
    def active(cls) -> frozenset["ExecutionStatus"]:
        """Return states that may still be resumed after checkpoint validation."""
        return frozenset(
            {
                cls.RUNNING,
                cls.PAUSED_BUDGET,
                cls.PAUSED_ERROR,
                cls.PAUSED_CONFIRMATION,
                cls.PAUSED_RECOVERY,
            }
        )


class CheckpointState(StrEnum):
    """Relationship between a business Execution and LangGraph checkpoint."""

    UNINITIALIZED = "uninitialized"
    AVAILABLE = "available"
    CLEANUP_PENDING = "cleanup_pending"
    CLEANED = "cleaned"
    MISSING = "missing"
