"""Foreground execution loop for one Agent turn or resume grant."""

from collections.abc import Callable, Iterator

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
    failed_turn_event,
    paused_turn_event,
)
from src.core.agent.slices import SliceExecutionService
from src.core.errors import ErrorAction
from src.core.errors.provider_failure import ProviderFailureService
from src.core.state.contracts import StateStore
from src.core.state.types import ExecutionStatus
from src.core.tasks.context import ToolExecutionContext
from src.core.telemetry import (
    bind_context,
    bind_run_context,
    emit_event,
    record_error,
    reset_context,
)
from src.core.tracing import (
    TraceDirection,
    TraceLayer,
    bind_trace_context,
    record_trace,
    reset_trace_context,
)
from src.core.workspace.models import SessionContext


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
        max_auto_slices: int = MAX_AUTO_SLICES_PER_GRANT,
    ) -> None:
        self.state_store_factory = state_store_factory
        self.turn_coordinator = turn_coordinator
        self.run_limits = run_limits
        self.execution_repository = execution_repository
        self.slice_execution_service = slice_execution_service
        self.provider_failure_service = provider_failure_service
        self.max_auto_slices = max(1, int(max_auto_slices))

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
            record_trace(
                TraceDirection.INTERNAL,
                TraceLayer.AGENT,
                "agent.run_started",
                data={"turn_index": current_turn, "resume": resume},
            )
            emit_event(
                "turn_started",
                "agent_service",
                "Started workspace Agent turn.",
                {
                    "session_name": session.session_name,
                    "user_input_preview": user_input[:300],
                    "limits": {
                        "max_graph_steps": run_context.limits.max_graph_steps,
                        "max_tool_calls": run_context.limits.max_tool_calls,
                    },
                },
            )
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
                    item = slice_result.error_item
                    if execution is not None and self.execution_repository is not None:
                        usage = budget.snapshot()
                        if item["data"].get("error_action") == ErrorAction.TERMINATE:
                            self.provider_failure_service.terminate_execution_after_error(
                                session,
                                execution,
                                item["data"].get(
                                    "error_category",
                                    StopReason.TURN_ERROR.value,
                                ),
                            )
                            yield from self.provider_failure_service.emit_terminal_provider_error(
                                session,
                                execution,
                                run_id,
                                item,
                            )
                            return
                        self.execution_repository.pause(
                            execution.execution_id,
                            ExecutionStatus.PAUSED_ERROR,
                            item["data"].get(
                                "stop_reason",
                                StopReason.TURN_ERROR.value,
                            ),
                            item["data"].get("message", ""),
                            usage=usage,
                        )
                    terminated = item["data"].get("error_action") == ErrorAction.TERMINATE
                    emit_event(
                        "turn_terminated" if terminated else "turn_paused",
                        "agent_service",
                        (
                            "Workspace Agent execution terminated after a "
                            "non-retryable error."
                            if terminated
                            else "Workspace Agent execution paused after an error."
                        ),
                        {
                            "stop_reason": item["data"].get(
                                "stop_reason",
                                StopReason.TURN_ERROR.value,
                            ),
                            "error_type": item["data"].get("type", "unknown"),
                            "error_category": item["data"].get(
                                "error_category",
                                "unknown",
                            ),
                        },
                        level="error",
                    )
                    record_trace(
                        TraceDirection.INTERNAL,
                        TraceLayer.AGENT,
                        "agent.run_failed",
                        data={"stop_reason": item["data"].get("stop_reason")},
                    )
                    yield item
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
                    record_trace(
                        TraceDirection.INTERNAL,
                        TraceLayer.AGENT,
                        "agent.slice_finished",
                        slice_id=slice_id,
                        data={"status": "completed"},
                    )
                    emit_event(
                        "turn_finished",
                        "agent_service",
                        "Finished workspace Agent turn.",
                        {
                            "stop_reason": StopReason.COMPLETED.value,
                            "tool_call_count": total_tool_calls,
                            "slice_count": slice_number,
                        },
                    )
                    record_trace(
                        TraceDirection.INTERNAL,
                        TraceLayer.AGENT,
                        "agent.run_finished",
                        data={"status": "ok", "slice_count": slice_number},
                    )
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

            snapshot = budget.snapshot()
            summary = (
                f"Execution paused because {exhausted_reason}. "
                f"Used {slice_number} Slice(s), {snapshot['tool_calls']} tool call(s), "
                f"{snapshot['controlled_executions']} controlled execution(s), and "
                f"{snapshot['delegations']} delegation(s)."
            )
            if execution is not None and self.execution_repository is not None:
                self.execution_repository.pause(
                    execution.execution_id,
                    ExecutionStatus.PAUSED_CONFIRMATION
                    if exhausted_reason == StopReason.BUDGET_LIMIT.value
                    else ExecutionStatus.PAUSED_BUDGET,
                    exhausted_reason,
                    summary,
                    usage=snapshot,
                )
            emit_event(
                "turn_paused",
                "agent_service",
                summary,
                {"slice_count": slice_number, **snapshot},
            )
            record_trace(
                TraceDirection.INTERNAL,
                TraceLayer.AGENT,
                "agent.run_paused",
                data={"stop_reason": exhausted_reason, "slice_count": slice_number},
            )
            yield paused_turn_event(
                session=session,
                run_id=run_id,
                execution=execution,
                stop_reason=exhausted_reason,
                tool_call_count=total_tool_calls,
                slices_used=slice_number,
                message=summary,
            )
        except Exception as exc:
            if execution is not None and self.execution_repository is not None:
                try:
                    usage = budget.snapshot() if budget is not None else None
                    if active_slice_id is not None:
                        self.execution_repository.finish_slice(
                            active_slice_id,
                            execution.execution_id,
                            status=ExecutionStatus.PAUSED_ERROR,
                            stop_reason=StopReason.TURN_ERROR.value,
                            usage=usage,
                        )
                    self.execution_repository.pause(
                        execution.execution_id,
                        ExecutionStatus.PAUSED_ERROR,
                        StopReason.TURN_ERROR.value,
                        str(exc),
                        usage=usage,
                    )
                except Exception:
                    pass
            record_error("agent_service", "turn", exc, "Agent turn failed.", event_type="turn_failed")
            record_trace(
                TraceDirection.INTERNAL,
                TraceLayer.AGENT,
                "agent.run_failed",
                data={"error_type": type(exc).__name__},
            )
            yield failed_turn_event(run_id, str(exc))
        finally:
            if run_context_token is not None:
                reset_context(run_context_token)
            if execution_trace_token is not None:
                reset_trace_context(execution_trace_token)
            if budget_token is not None:
                reset_execution_budget(budget_token)
            reset_context(context_token)
            store.close()
