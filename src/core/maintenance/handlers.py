"""Concrete maintenance strategies composed by CoreApp."""

from collections.abc import Callable

from src.core.maintenance.models import MaintenanceJob
from src.core.state.contracts import CheckpointStore, MaintenanceStateStore
from src.core.state.executions import ExecutionRepository
from src.core.telemetry import emit_event
from src.core.workspace.contracts import WorkspaceIdentityRepository


class ContextSummaryHandler:
    """Generate and CAS-write a derived Session summary."""

    def __init__(
        self,
        workspace_repository: WorkspaceIdentityRepository,
        store_factory: Callable[[], MaintenanceStateStore],
        context_manager,
    ) -> None:
        self.workspace_repository = workspace_repository
        self.store_factory = store_factory
        self.context_manager = context_manager

    def __call__(self, job: MaintenanceJob) -> None:
        session = self.workspace_repository.get_session(job.workspace_id, job.session_id)
        store = self.store_factory()
        try:
            previous_summary, summary_through, indexed_messages = store.load_summary_source(
                session,
                int(job.payload["target_turn"]),
            )
            messages = [message for _turn, message in indexed_messages]
            if not self.context_manager.should_summarize(messages):
                return
            retained = indexed_messages[-self.context_manager.recent_message_limit:]
            if not retained:
                return
            # summary_through_turn is a Turn boundary, so never summarize only
            # part of a Turn and then make the remaining messages unreachable.
            earliest_retained_turn = retained[0][0]
            old = [item for item in indexed_messages if item[0] < earliest_retained_turn]
            if not old:
                return
            through_turn = old[-1][0]
            summary = self.context_manager.summarize_messages(
                previous_summary,
                [message for _turn, message in old],
            )
            updated = store.update_summary_cas(
                session,
                expected_summary_through_turn=summary_through,
                summary_through_turn=through_turn,
                summary=summary,
            )
            emit_event(
                "context_summary_maintenance_finished",
                "maintenance",
                "Background context summary maintenance finished.",
                {"updated": updated, "summary_through_turn": through_turn},
            )
        finally:
            store.close()


class MemoryExtractionHandler:
    """Extract long-term memory from one already committed Turn."""

    def __init__(
        self,
        workspace_repository: WorkspaceIdentityRepository,
        store_factory: Callable[[], MaintenanceStateStore],
    ) -> None:
        self.workspace_repository = workspace_repository
        self.store_factory = store_factory

    def __call__(self, job: MaintenanceJob) -> None:
        session = self.workspace_repository.get_session(job.workspace_id, job.session_id)
        turn_index = int(job.payload["turn_index"])
        store = self.store_factory()
        try:
            messages, source_ids = store.load_turn_messages(session, turn_index)
            store.extract_and_save_memories(session, turn_index, messages, source_ids)
        finally:
            store.close()


class CheckpointCleanupHandler:
    """Delete one no-longer-needed checkpoint and update the business state."""

    def __init__(
        self,
        checkpoint_manager: CheckpointStore,
        execution_repository: ExecutionRepository,
    ) -> None:
        self.checkpoint_manager = checkpoint_manager
        self.execution_repository = execution_repository

    def __call__(self, job: MaintenanceJob) -> None:
        self.checkpoint_manager.delete_thread(str(job.payload["checkpoint_thread_id"]))
        if job.execution_id:
            self.execution_repository.mark_checkpoint_cleaned(job.execution_id)
