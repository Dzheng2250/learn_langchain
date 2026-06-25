"""Concrete maintenance strategies composed by CoreApp."""

from src.core.maintenance.models import MaintenanceJob
from src.core.ports import (
    CheckpointStore,
    ConversationHistoryStore,
    MemoryWriteStore,
    SummaryMaintenanceStore,
)
from src.core.state.executions import ExecutionRepository
from src.core.telemetry import emit_event
from src.core.workspace.contracts import WorkspaceIdentityRepository


class ContextSummaryHandler:
    """Generate and CAS-write a derived Session summary."""

    def __init__(
        self,
        workspace_repository: WorkspaceIdentityRepository,
        summary_store: SummaryMaintenanceStore,
        context_manager,
    ) -> None:
        self.workspace_repository = workspace_repository
        self.summary_store = summary_store
        self.context_manager = context_manager

    def __call__(self, job: MaintenanceJob) -> None:
        session = self.workspace_repository.get_session(job.workspace_id, job.session_id)
        previous_summary, summary_through, indexed_messages = (
            self.summary_store.load_summary_source(
                session,
                int(job.payload["target_turn"]),
            )
        )
        messages = [message for _turn, message in indexed_messages]
        turn_indexes = []
        for turn_index, _message in indexed_messages:
            if not turn_indexes or turn_indexes[-1] != turn_index:
                turn_indexes.append(turn_index)
        try:
            should_summarize = self.context_manager.should_summarize(
                messages,
                turn_count=len(turn_indexes),
            )
        except TypeError:
            should_summarize = self.context_manager.should_summarize(messages)
        if not should_summarize:
            return
        recent_turn_limit = getattr(
            self.context_manager,
            "recent_turn_limit",
            self.context_manager.recent_message_limit,
        )
        retained_turns = set(turn_indexes[-recent_turn_limit:])
        if not retained_turns:
            return
        # summary_through_turn is a Turn boundary, so never summarize only
        # part of a Turn and then make the remaining messages unreachable.
        earliest_retained_turn = min(retained_turns)
        old = [item for item in indexed_messages if item[0] < earliest_retained_turn]
        if not old:
            return
        through_turn = old[-1][0]
        summary = self.context_manager.summarize_messages(
            previous_summary,
            [message for _turn, message in old],
        )
        updated = self.summary_store.update_summary_cas(
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


class MemoryExtractionHandler:
    """Extract long-term memory from one already committed Turn."""

    def __init__(
        self,
        workspace_repository: WorkspaceIdentityRepository,
        history_store: ConversationHistoryStore,
        memory_store: MemoryWriteStore,
    ) -> None:
        self.workspace_repository = workspace_repository
        self.history_store = history_store
        self.memory_store = memory_store

    def __call__(self, job: MaintenanceJob) -> None:
        session = self.workspace_repository.get_session(job.workspace_id, job.session_id)
        turn_index = int(job.payload["turn_index"])
        messages, source_ids = self.history_store.load_turn(session, turn_index)
        self.memory_store.extract_and_save(
            session,
            turn_index,
            messages,
            source_ids,
        )


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
