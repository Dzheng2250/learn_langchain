"""Lifecycle hooks for durable Agent service dependencies."""

from collections.abc import Callable

from src.core.agent.worker import TurnWorkerExecutor
from src.core.state.contracts import StateStore


class AgentServiceLifecycle:
    """Initialize and close resources that sit outside foreground Turn logic.

    `AgentTurnService` coordinates user-facing turns. Schema initialization,
    checkpoint storage, recovery reconciliation, and background maintenance are
    process lifecycle concerns, so they live behind this small collaborator.
    """

    def __init__(
        self,
        *,
        state_store_factory: Callable[[], StateStore],
        turn_worker: TurnWorkerExecutor,
        checkpoint_manager=None,
        maintenance_scheduler=None,
        recovery_coordinator=None,
    ) -> None:
        self.state_store_factory = state_store_factory
        self.turn_worker = turn_worker
        self.checkpoint_manager = checkpoint_manager
        self.maintenance_scheduler = maintenance_scheduler
        self.recovery_coordinator = recovery_coordinator

    def initialize(self) -> None:
        """Initialize durable schema dependencies before accepting requests."""
        store = self.state_store_factory()
        try:
            store.initialize()
            if self.checkpoint_manager is not None:
                self.checkpoint_manager.initialize()
            if self.recovery_coordinator is not None:
                self.recovery_coordinator.reconcile()
            if self.maintenance_scheduler is not None:
                self.maintenance_scheduler.start()
        finally:
            store.close()

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
