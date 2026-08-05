"""Lifecycle hooks for durable Agent service dependencies."""

from src.core.agent.worker import TurnWorkerExecutor
from src.core.ports import StateInitializer


class AgentServiceLifecycle:
    """Initialize and close resources that sit outside foreground Turn logic.

    `AgentTurnService` coordinates user-facing turns. Schema initialization,
    checkpoint storage, recovery reconciliation, and background maintenance are
    process lifecycle concerns, so they live behind this small collaborator.
    """

    def __init__(
        self,
        *,
        state_initializer: StateInitializer,
        turn_worker: TurnWorkerExecutor,
        checkpoint_manager=None,
        maintenance_scheduler=None,
        recovery_coordinator=None,
        tool_ledger=None,
    ) -> None:
        self.state_initializer = state_initializer
        self.turn_worker = turn_worker
        self.checkpoint_manager = checkpoint_manager
        self.maintenance_scheduler = maintenance_scheduler
        self.recovery_coordinator = recovery_coordinator
        self.tool_ledger = tool_ledger

    def initialize(self) -> None:
        """Initialize durable schema dependencies before accepting requests."""
        self.state_initializer.initialize()
        if self.tool_ledger is not None:
            self.tool_ledger.mark_running_uncertain()
        if self.checkpoint_manager is not None:
            self.checkpoint_manager.initialize()
        if self.recovery_coordinator is not None:
            self.recovery_coordinator.reconcile()
        if self.maintenance_scheduler is not None:
            self.maintenance_scheduler.start()

    def close(self) -> None:
        """Stop foreground workers, then close background durable resources."""
        self.turn_worker.close()
        maintenance_stopped = (
            self.maintenance_scheduler.close()
            if self.maintenance_scheduler is not None
            else True
        )
        if self.checkpoint_manager is not None and maintenance_stopped:
            self.checkpoint_manager.close()
