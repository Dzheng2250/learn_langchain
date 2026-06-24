"""Unit of Work for one completed Agent Turn."""

from src.core.finalization.models import CompletedTurn
from src.core.ports import StateUnitOfWorkFactory


class CompletedTurnCommitter:
    """Atomically commit business facts through an injected persistence port."""

    def __init__(self, unit_of_work_factory: StateUnitOfWorkFactory) -> None:
        self.unit_of_work_factory = unit_of_work_factory

    def commit(self, completed: CompletedTurn) -> list[str]:
        """Commit messages, Session state, Execution state, and outbox jobs."""
        with self.unit_of_work_factory.begin() as uow:
            message_ids = uow.history.append_turn(completed)
            uow.sessions.save_fast_context(completed)
            uow.executions.finish_completed_turn(completed)
            for job in completed.jobs:
                uow.maintenance.enqueue(job)
            uow.commit()
        return message_ids
