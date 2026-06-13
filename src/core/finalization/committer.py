"""Unit of Work for one completed Agent Turn."""

from src.core.finalization.models import CompletedTurn
from src.core.maintenance.repository import MaintenanceRepository
from src.core.state.contracts import StateStore
from src.core.state.database import LocalStateDatabase
from src.core.state.executions import ExecutionRepository


class CompletedTurnCommitter:
    """Atomically commit business facts and their required maintenance tasks."""

    def __init__(
        self,
        database: LocalStateDatabase,
        execution_repository: ExecutionRepository,
        maintenance_repository: MaintenanceRepository,
    ) -> None:
        self.database = database
        self.execution_repository = execution_repository
        self.maintenance_repository = maintenance_repository

    def commit(self, store: StateStore, completed: CompletedTurn) -> list[str]:
        """Commit messages, Session state, Execution state, and outbox jobs."""
        with self.database.transaction() as conn:
            message_ids = store.append_messages_in_transaction(
                conn,
                completed.session,
                completed.turn_index,
                completed.messages,
                execution_id=completed.execution_id,
            )
            store.save_fast_session_in_transaction(
                conn,
                completed.session,
                completed.state,
                completed.turn_index,
            )
            if completed.execution_id:
                if completed.slice_id:
                    self.execution_repository.finish_slice_in_transaction(
                        conn,
                        completed.slice_id,
                        completed.execution_id,
                        graph_steps_used=completed.graph_steps_used,
                        usage=completed.usage,
                    )
                self.execution_repository.complete_in_transaction(
                    conn,
                    completed.session,
                    completed.execution_id,
                )
            for job in completed.jobs:
                self.maintenance_repository.enqueue_in_transaction(conn, job)
        return message_ids
