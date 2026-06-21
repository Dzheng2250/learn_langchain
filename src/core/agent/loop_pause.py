"""Pause handling for bounded foreground Agent execution."""

from src.core.agent.budget import ExecutionBudget
from src.core.agent.models import StopReason
from src.core.agent.responses import paused_turn_event
from src.core.agent.run_observer import TurnRunObserver
from src.core.ports import ExecutionPauseStore
from src.core.state.types import ExecutionStatus
from src.core.workspace.models import SessionContext


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
    ) -> dict:
        """Persist a recoverable pause and return the user-visible event."""
        snapshot = budget.snapshot()
        summary = (
            f"Execution paused because {exhausted_reason}. "
            f"Used {slice_number} Slice(s), {snapshot['tool_calls']} tool call(s), "
            f"{snapshot['controlled_executions']} controlled execution(s), and "
            f"{snapshot['delegations']} delegation(s)."
        )
        if execution is not None and self.execution_store is not None:
            self.execution_store.pause(
                execution.execution_id,
                ExecutionStatus.PAUSED_CONFIRMATION
                if exhausted_reason == StopReason.BUDGET_LIMIT.value
                else ExecutionStatus.PAUSED_BUDGET,
                exhausted_reason,
                summary,
                usage=snapshot,
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
        )

