"""Maintenance queue helpers for Session checkpoint cleanup."""

from src.core.maintenance.models import MaintenanceJobSpec
from src.core.maintenance.types import MaintenanceJobType, MaintenancePriority
from src.core.workspace.models import SessionContext


class SessionCheckpointCleanupQueue:
    """Queue checkpoint cleanup jobs without exposing maintenance details upstream."""

    def __init__(self, maintenance_repository=None, maintenance_scheduler=None) -> None:
        self.maintenance_repository = maintenance_repository
        self.maintenance_scheduler = maintenance_scheduler

    def enqueue(self, session: SessionContext, pending) -> bool:
        """Queue cleanup for a discarded Execution and wake the scheduler when needed."""
        if self.maintenance_repository is None:
            return False
        self.maintenance_repository.enqueue(
            MaintenanceJobSpec(
                MaintenanceJobType.CHECKPOINT_CLEANUP,
                f"checkpoint_cleanup:{pending.execution_id}",
                str(session.workspace.workspace_id),
                str(session.session_id),
                {"checkpoint_thread_id": pending.checkpoint_thread_id},
                execution_id=pending.execution_id,
                priority=MaintenancePriority.CHECKPOINT_CLEANUP,
            )
        )
        if self.maintenance_scheduler is not None:
            self.maintenance_scheduler.wake()
        return True
