"""Foreground execution loop for one Agent turn or resume grant."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from src.config.settings import MAX_AUTO_SLICES_PER_GRANT
from src.core.agent.budget import (
    ExecutionBudget,
    bind_execution_budget,
    reset_execution_budget,
)
from src.core.agent.contracts import ExecutionControl
from src.core.agent.coordinator import TurnCoordinator
from src.core.agent.models import RunLimits, StopReason
from src.core.agent.responses import (
    completed_turn_event,
)
from src.core.agent.loop_errors import TurnLoopErrorHandler
from src.core.agent.loop_pause import TurnLoopPauseHandler
from src.core.agent.run_observer import TurnRunObserver
from src.core.agent.slices import SliceExecutionService
from src.core.errors.provider_failure import ProviderFailureService
from src.core.state.contracts import StateStore
from src.core.state.types import ExecutionStatus
from src.core.tasks.context import ToolExecutionContext
from src.core.telemetry import (
    bind_context,
    bind_run_context,
    reset_context,
)
from src.core.tracing import (
    bind_trace_context,
    reset_trace_context,
)
from src.core.workspace.models import SessionContext


@dataclass(frozen=True)
class LoopConfig:
    """Stable scalar configuration for the foreground Slice loop."""

    max_auto_slices: int = MAX_AUTO_SLICES_PER_GRANT


class TurnExecutionLoop:
    """Run the bounded Slice loop for a foreground Agent turn.

    This class owns the stateful graph execution loop: context binding, budget
    tracking, Slice continuation, finalization, and recoverable pause/error
    persistence. AgentTurnService delegates here after it has resolved the
    Workspace, Session, runtime graph, and Execution identity.
    """

    def __init__(
        self,
        *,
        state_store_factory: Callable[[], StateStore],
        turn_coordinator: TurnCoordinator,
        run_limits: RunLimits,
        execution_repository=None,
        slice_execution_service: SliceExecutionService,
        provider_failure_service: ProviderFailureService,
        observer: TurnRunObserver | None = None,
        error_handler: TurnLoopErrorHandler | None = None,
        pause_handler: TurnLoopPauseHandler | None = None,
        config: LoopConfig | None = None,
        max_auto_slices: int = MAX_AUTO_SLICES_PER_GRANT,
    ) -> None:
        self.state_store_factory = state_store_factory
        self.turn_coordinator = turn_coordinator
        self.run_limits = run_limits
        self.execution_repository = execution_repository
        self.slice_execution_service = slice_execution_service
        self.provider_failure_service = provider_failure_service
        self.observer = observer or TurnRunObserver()
        self.error_handler = error_handler or TurnLoopErrorHandler(
            execution_repository=execution_repository,
            provider_failure_service=provider_failure_service,
            observer=self.observer,
        )
        self.pause_handler = pause_handler or TurnLoopPauseHandler(
            execution_repository=execution_repository,
            observer=self.observer,
        )
        self.config = config or LoopConfig(max_auto_slices=max_auto_slices)
        self.max_auto_slices = max(1, int(self.config.max_auto_slices))

    def stream_locked_turn(
        self,
        session: SessionContext,
        graph,
        user_input: str,
        run_id: str,
        *,
        execution=None,
        resume: bool = False,
        control: ExecutionControl | None = None,
    ) -> Iterator[dict]:
        """Run bounded Slices and persist either completion or recoverable pause."""
        store = self.state_store_factory()
        context_token = bind_context(
            workspace_id=session.workspace.workspace_id,
            session_id=session.session_id,
            run_id=run_id,
        )
        run_context_token = None
        execution_trace_token = None
        budget_token = None
        budget = None
        active_slice_id = None
        try:
            # Persisted turn_index identifies the last completed turn. The
            # current turn is assigned only after the Session lock is held.
            prepared = self.turn_coordinator.prepare(
                store=store,
                session=session,
                user_input=user_input,
                run_id=run_id,
                limits=self.run_limits,
            )
            state = prepared.state
            current_turn = prepared.turn_index
            run_context = prepared.run_context
            run_context_token = bind_run_context(run_context)
            if execution is not None:
                execution_trace_token = bind_trace_context(execution_id=execution.execution_id)
            self.observer.run_started(session, user_input, run_context, current_turn, resume)
            input_messages = prepared.input_messages
            checkpoint_thread_id = execution.checkpoint_thread_id if execution else None
            tool_context = ToolExecutionContext(
                workspace_id=str(session.workspace.workspace_id),
                session_id=str(session.session_id),
                execution_id=execution.execution_id if execution else None,
            )
            total_tool_calls = 0
            budget = ExecutionBudget()
            budget_token = bind_execution_budget(budget)
            exhausted_reason = StopReason.GRAPH_STEP_LIMIT.value
            for slice_number in range(1, self.max_auto_slices + 1):
                if budget.wall_time_exhausted():
                    exhausted_reason = StopReason.GRANT_WALL_TIME_LIMIT.value
                    break
                slice_input = None if resume or slice_number > 1 else input_messages
                paused_for_budget = False
                slice_result = yield from self.slice_execution_service.stream_slice(
                    graph=graph,
                    slice_input=slice_input,
                    run_context=run_context,
                    execution=execution,
                    slice_number=slice_number,
                    checkpoint_thread_id=checkpoint_thread_id,
                    budget=budget,
                    tool_context=tool_context,
                )
                total_tool_calls += slice_result.tool_call_count
                slice_id = slice_result.slice_id
                if slice_result.paused_for_budget:
                    paused_for_budget = True
                    exhausted_reason = (
                        slice_result.exhausted_reason
                        or StopReason.GRAPH_STEP_LIMIT.value
                    )
                elif slice_result.error_item is not None:
                    yield from self.error_handler.stream_slice_error(
                        session=session,
                        execution=execution,
                        run_id=run_id,
                        item=slice_result.error_item,
                        budget=budget,
                    )
                    return
                elif slice_result.done_item is not None:
                    item = slice_result.done_item
                    final_messages = item["data"]["messages"]
                    active_slice_id = slice_id
                    finalization = self.turn_coordinator.finalize(
                        store=store,
                        session=session,
                        turn_index=current_turn,
                        previous_state=state,
                        final_messages=final_messages,
                        user_input=user_input,
                        execution=execution,
                        slice_id=slice_id,
                        graph_steps_used=int(item["data"].get("graph_steps_used", 0)),
                        usage=budget.snapshot(),
                    )
                    snapshot = budget.snapshot()
                    active_slice_id = None
                    self.observer.slice_finished(slice_id)
                    self.observer.run_finished(total_tool_calls, slice_number)
                    yield completed_turn_event(
                        session=session,
                        run_id=run_id,
                        execution=execution,
                        tool_call_count=total_tool_calls,
                        slices_used=slice_number,
                        finalization=finalization,
                        context_tokens=snapshot.get("input_tokens", 0),
                    )
                    return
                if not paused_for_budget:
                    return
                if exhausted_reason == StopReason.BUDGET_LIMIT.value:
                    break
                if checkpoint_thread_id is None:
                    # Compatibility services without a checkpointer cannot
                    # safely continue from input=None. Production Core always
                    # provides a durable checkpoint thread.
                    break
                if control is not None and control.pause_after_slice.is_set():
                    exhausted_reason = StopReason.CLIENT_DISCONNECTED.value
                    break
                if budget.wall_time_exhausted():
                    exhausted_reason = StopReason.GRANT_WALL_TIME_LIMIT.value
                    break
                resume = True

            yield self.pause_handler.pause_event(
                session=session,
                run_id=run_id,
                execution=execution,
                budget=budget,
                exhausted_reason=exhausted_reason,
                slice_number=slice_number,
                total_tool_calls=total_tool_calls,
            )
        except Exception as exc:
            yield from self.error_handler.stream_unexpected_exception(
                run_id=run_id,
                execution=execution,
                active_slice_id=active_slice_id,
                budget=budget,
                exc=exc,
            )
        finally:
            if run_context_token is not None:
                reset_context(run_context_token)
            if execution_trace_token is not None:
                reset_trace_context(execution_trace_token)
            if budget_token is not None:
                reset_execution_budget(budget_token)
            reset_context(context_token)
            store.close()
