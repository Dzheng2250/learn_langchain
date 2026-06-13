"""Saga coordinator that reconciles business Execution and checkpoint state."""

from src.core.maintenance.models import MaintenanceJobSpec
from src.core.maintenance.repository import MaintenanceRepository
from src.core.maintenance.types import MaintenanceJobType, MaintenancePriority
from src.core.state.contracts import CheckpointStore
from src.core.state.executions import ExecutionRepository
from src.core.state.types import ExecutionStatus
from src.core.telemetry import emit_event


class ExecutionRecoveryCoordinator:
    """Repair recoverable cross-database intermediate states during Core startup."""

    def __init__(
        self,
        execution_repository: ExecutionRepository,
        checkpoint_manager: CheckpointStore,
        maintenance_repository: MaintenanceRepository,
    ) -> None:
        self.execution_repository = execution_repository
        self.checkpoint_manager = checkpoint_manager
        self.maintenance_repository = maintenance_repository

    def reconcile(self) -> dict[str, int]:
        """Reconcile all known executions using idempotent state transitions."""
        result = {"paused_recovery": 0, "missing": 0, "cleanup_enqueued": 0}
        for execution in self.execution_repository.list_for_recovery():
            status = execution["status"]
            if status in ExecutionStatus.active():
                exists = self.checkpoint_manager.thread_exists(
                    execution["checkpoint_thread_id"]
                )
                if exists:
                    if status == ExecutionStatus.RUNNING:
                        self.execution_repository.mark_paused_recovery(execution["execution_id"])
                        result["paused_recovery"] += 1
                else:
                    self.execution_repository.mark_checkpoint_missing(execution["execution_id"])
                    result["missing"] += 1
            elif status in {ExecutionStatus.COMPLETED, ExecutionStatus.DISCARDED}:
                # Cleanup is idempotent, so no checkpoint read is required.
                # Avoid scanning checkpoints for every historical completion.
                dedupe_key = f"checkpoint_cleanup:{execution['execution_id']}"
                self.maintenance_repository.enqueue(
                    MaintenanceJobSpec(
                        MaintenanceJobType.CHECKPOINT_CLEANUP,
                        dedupe_key,
                        execution["workspace_id"],
                        execution["session_id"],
                        {"checkpoint_thread_id": execution["checkpoint_thread_id"]},
                        execution_id=execution["execution_id"],
                        priority=MaintenancePriority.CHECKPOINT_CLEANUP,
                    )
                )
                self.maintenance_repository.requeue_failed(dedupe_key)
                result["cleanup_enqueued"] += 1
        emit_event(
            "execution_recovery_reconciled",
            "execution_recovery",
            "Reconciled business Execution and checkpoint state.",
            result,
        )
        return result
