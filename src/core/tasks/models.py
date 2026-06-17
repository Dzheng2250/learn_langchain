"""Domain models for Agent-private execution tasks."""

from dataclasses import dataclass, field
from enum import StrEnum


class TaskStatus(StrEnum):
    """Lifecycle states for one private task."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    @classmethod
    def blocking(cls) -> frozenset["TaskStatus"]:
        """Return dependency states that still block downstream work."""
        return frozenset({cls.PENDING, cls.IN_PROGRESS})


@dataclass(frozen=True)
class TaskPlanItem:
    """One task item proposed by the parent Agent."""

    task_key: str
    subject: str
    description: str = ""
    notes: str = ""
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    ordinal: int = 0


@dataclass(frozen=True)
class ExecutionTask:
    """Task row plus resolved dependency keys."""

    task_id: str
    execution_id: str
    task_key: str
    subject: str
    description: str
    status: TaskStatus
    notes: str
    ordinal: int
    version: int
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    blocked_by: tuple[str, ...] = field(default_factory=tuple)
    created_at: str = ""
    updated_at: str = ""
    completed_at: str | None = None

    @property
    def ready(self) -> bool:
        """Return whether the task can be started according to dependencies."""
        return self.status == TaskStatus.PENDING and not self.blocked_by
