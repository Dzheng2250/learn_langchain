"""Prepare and commit only the work required before releasing a response."""

from src.core.finalization.models import CompletedTurn, FinalizationResult
from src.core.maintenance.models import MaintenanceJobSpec
from src.core.maintenance.types import MaintenanceJobType, MaintenancePriority
from src.core.memory.policy import has_explicit_memory_request, memory_extraction_reason
from src.core.telemetry import event_span, record_error


class TurnFinalizer:
    """Separate minimal durable commit from slower derived maintenance."""

    def __init__(
        self,
        context_manager,
        committer,
        maintenance_scheduler,
        *,
        memory_enabled: bool = True,
    ) -> None:
        self.context_manager = context_manager
        self.committer = committer
        self.maintenance_scheduler = maintenance_scheduler
        self.memory_enabled = memory_enabled

    def finalize(
        self,
        *,
        session,
        turn_index: int,
        previous_state,
        final_messages: list,
        user_input: str,
        execution=None,
        slice_id: str | None = None,
        graph_steps_used: int = 0,
        usage: dict | None = None,
    ) -> FinalizationResult:
        """Commit a completed Turn without invoking LLM-backed maintenance."""
        messages = self.context_manager.extract_turn_messages(previous_state, final_messages)
        fast_state = self.context_manager.build_fast_state(previous_state, final_messages)
        # Update context_tokens with this turn's actual LLM input_tokens so
        # the maintenance handler and next session.status see the real value.
        if usage and usage.get("input_tokens"):
            fast_state.context_tokens = int(usage["input_tokens"])
        workspace_id = str(session.workspace.workspace_id)
        session_id = str(session.session_id)
        jobs = [
            MaintenanceJobSpec(
                MaintenanceJobType.CONTEXT_SUMMARY,
                f"context_summary:{session_id}:{turn_index}",
                workspace_id,
                session_id,
                {"target_turn": turn_index},
                priority=MaintenancePriority.CONTEXT_SUMMARY,
            )
        ]
        memory_reason = memory_extraction_reason(user_input, turn_index, messages)
        explicit_memory = has_explicit_memory_request(user_input)
        if self.memory_enabled and memory_reason not in {"not_triggered", "disabled"}:
            jobs.append(
                MaintenanceJobSpec(
                    MaintenanceJobType.MEMORY_EXTRACT,
                    f"memory_extract:{session_id}:{turn_index}",
                    workspace_id,
                    session_id,
                    {"turn_index": turn_index, "reason": memory_reason},
                    priority=(
                        MaintenancePriority.EXPLICIT_MEMORY
                        if explicit_memory
                        else MaintenancePriority.NORMAL_MEMORY
                    ),
                )
            )
        if execution is not None:
            jobs.append(
                MaintenanceJobSpec(
                    MaintenanceJobType.CHECKPOINT_CLEANUP,
                    f"checkpoint_cleanup:{execution.execution_id}",
                    workspace_id,
                    session_id,
                    {"checkpoint_thread_id": execution.checkpoint_thread_id},
                    execution_id=execution.execution_id,
                    priority=MaintenancePriority.CHECKPOINT_CLEANUP,
                )
            )
        completed = CompletedTurn(
            session=session,
            turn_index=turn_index,
            messages=messages,
            state=fast_state,
            execution_id=execution.execution_id if execution else None,
            checkpoint_thread_id=execution.checkpoint_thread_id if execution else None,
            slice_id=slice_id,
            graph_steps_used=graph_steps_used,
            usage=usage or {},
            jobs=tuple(jobs),
        )
        with event_span(
            "completed_turn_commit",
            "turn_finalizer",
            payload={
                "turn_index": turn_index,
                "message_count": len(messages),
                "maintenance_job_count": len(jobs),
            },
        ):
            message_ids = self.committer.commit(completed)
        try:
            self.maintenance_scheduler.wake()
        except Exception as exc:
            # Jobs are already durable in state.db. A failed wake-up must not
            # turn a committed response into an error; polling or restart will
            # eventually claim the work.
            record_error(
                "turn_finalizer",
                "maintenance_wake",
                exc,
                "Completed Turn committed, but maintenance wake-up failed.",
                {"turn_index": turn_index, "maintenance_job_count": len(jobs)},
            )
        return FinalizationResult(
            tuple(message_ids),
            "pending" if jobs else "none",
            "pending"
            if any(job.job_type == MaintenanceJobType.MEMORY_EXTRACT for job in jobs)
            else "not_scheduled",
            explicit_memory,
        )
