"""Pause handling for bounded foreground Agent execution."""

from src.core.agent.budget import ExecutionBudget
from src.core.agent.models import StopReason
from src.core.agent.responses import paused_turn_event
from src.core.agent.run_observer import TurnRunObserver
from src.core.ports import ExecutionPauseStore
from src.core.state.types import ExecutionStatus
from src.core.workspace.models import SessionContext
from src.core.llm.usage import context_tokens


class TurnLoopPauseHandler:
    """Persist a budget pause and build the matching stream event."""

    def __init__(self, *, execution_store: ExecutionPauseStore | None, observer: TurnRunObserver) -> None:
        self.execution_store = execution_store
        self.observer = observer

    def pause_event(
        self,
        *,
        session: SessionContext,
        run_id: str,
        execution,
        budget: ExecutionBudget,
        exhausted_reason: str,
        slice_number: int,
        total_tool_calls: int,
        pause_data: dict | None = None,
    ) -> dict:
        """Persist a recoverable pause and return the user-visible event."""
        snapshot = budget.snapshot()
        pause_data = dict(pause_data or {})
        cursor = str(pause_data.get("tool_call_id") or "")
        checkpoint = str(pause_data.get("checkpoint_fingerprint") or "")
        fingerprint = ":".join(
            part for part in (exhausted_reason, checkpoint, cursor) if part
        )
        summary = (
            f"Execution paused because {exhausted_reason}. "
            f"Used {slice_number} Slice(s), {snapshot['tool_calls']} tool call(s), "
            f"{snapshot['controlled_executions']} controlled execution(s), and "
            f"{snapshot['delegations']} delegation(s)."
        )
        if execution is not None and self.execution_store is not None:
            self.execution_store.pause(
                execution.execution_id,
                (
                    ExecutionStatus.PAUSED_CONFIRMATION
                    if exhausted_reason == StopReason.TOOL_APPROVAL.value
                    else (
                        ExecutionStatus.PAUSED_RECOVERY
                        if exhausted_reason == StopReason.TOOL_RECOVERY_REQUIRED.value
                        else ExecutionStatus.PAUSED_BUDGET
                    )
                ),
                exhausted_reason,
                summary,
                usage=snapshot,
                pause_fingerprint=fingerprint,
                pause_metadata=pause_data,
            )
        self.observer.run_paused(summary, snapshot, slice_number, exhausted_reason)
        return paused_turn_event(
            session=session,
            run_id=run_id,
            execution=execution,
            stop_reason=exhausted_reason,
            tool_call_count=total_tool_calls,
            slices_used=slice_number,
            message=summary,
            context_tokens=context_tokens(snapshot),
        )
