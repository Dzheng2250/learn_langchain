"""Execution helper for one bounded LangGraph Slice."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from src.core.agent.budget import ExecutionBudget
from src.core.agent.models import AgentRunContext, StopReason
from src.core.errors import ProviderErrorHandler
from src.core.ports import ExecutionSliceStore
from src.core.state.types import ExecutionStatus
from src.core.streaming.events import stream_graph_events
from src.core.tasks.context import ToolExecutionContext
from src.core.tracing import (
    TraceDirection,
    TraceLayer,
    bind_trace_context,
    record_trace,
    reset_trace_context,
)


@dataclass(frozen=True)
class SliceExecutionResult:
    """Terminal outcome of one graph Slice."""

    slice_id: str | None
    paused_for_budget: bool = False
    exhausted_reason: str | None = None
    error_item: dict | None = None
    done_item: dict | None = None
    tool_call_count: int = 0


def _trace_slice(events, slice_id):
    """Bind one Slice identity around graph streaming and LLM callbacks."""
    token = bind_trace_context(slice_id=slice_id)
    try:
        yield from events
    finally:
        reset_trace_context(token)


class SliceExecutionService:
    """Start, stream, finish, and trace a single bounded graph Slice."""

    def __init__(
        self,
        *,
        execution_store: ExecutionSliceStore | None,
        provider_error_handler: ProviderErrorHandler,
    ) -> None:
        self.execution_store = execution_store
        self.provider_error_handler = provider_error_handler

    def stream_slice(
        self,
        *,
        graph: Any,
        slice_input,
        run_context: AgentRunContext,
        execution,
        slice_number: int,
        checkpoint_thread_id: str | None,
        budget: ExecutionBudget,
        tool_context: ToolExecutionContext,
    ) -> Iterator[dict]:
        """Stream one Slice and return a compact terminal result via ``yield from``."""
        slice_id = self._start_slice(execution, slice_number)
        record_trace(
            TraceDirection.INTERNAL,
            TraceLayer.AGENT,
            "agent.slice_started",
            slice_id=slice_id,
            data={"slice_number": slice_number},
        )
        tool_call_count = 0
        try:
            for item in _trace_slice(
                stream_graph_events(
                    graph,
                    slice_input,
                    run_context,
                    checkpoint_thread_id=checkpoint_thread_id,
                    provider_error_handler=self.provider_error_handler,
                    tool_context=tool_context,
                ),
                slice_id,
            ):
                if item["event"] in {"paused", "done"}:
                    tool_call_count += int(
                        item.get("data", {}).get("tool_call_count", 0)
                    )
                if item["event"] == "paused":
                    exhausted_reason = item["data"].get(
                        "stop_reason",
                        StopReason.GRAPH_STEP_LIMIT.value,
                    )
                    self._finish_slice(
                        slice_id,
                        execution,
                        status=(
                            ExecutionStatus.PAUSED_CONFIRMATION
                            if exhausted_reason == StopReason.TOOL_APPROVAL.value
                            else ExecutionStatus.PAUSED_BUDGET
                        ),
                        stop_reason=exhausted_reason,
                        graph_steps_used=int(item["data"].get("graph_steps_used", 0)),
                        usage=budget.snapshot(),
                    )
                    record_trace(
                        TraceDirection.INTERNAL,
                        TraceLayer.AGENT,
                        "agent.slice_finished",
                        slice_id=slice_id,
                        data={"status": "paused", "stop_reason": exhausted_reason},
                    )
                    return SliceExecutionResult(
                        slice_id=slice_id,
                        paused_for_budget=True,
                        exhausted_reason=exhausted_reason,
                        tool_call_count=tool_call_count,
                    )
                if item["event"] == "error":
                    stop_reason = item["data"].get(
                        "stop_reason",
                        StopReason.TURN_ERROR.value,
                    )
                    self._finish_slice(
                        slice_id,
                        execution,
                        status=ExecutionStatus.PAUSED_ERROR,
                        stop_reason=stop_reason,
                        graph_steps_used=int(item["data"].get("graph_steps_used", 0)),
                        usage=budget.snapshot(),
                    )
                    record_trace(
                        TraceDirection.INTERNAL,
                        TraceLayer.AGENT,
                        "agent.slice_finished",
                        slice_id=slice_id,
                        data={"status": "error", "stop_reason": stop_reason},
                    )
                    return SliceExecutionResult(
                        slice_id=slice_id,
                        error_item=item,
                        tool_call_count=tool_call_count,
                    )
                if item["event"] == "done":
                    return SliceExecutionResult(
                        slice_id=slice_id,
                        done_item=item,
                        tool_call_count=tool_call_count,
                    )
                yield item
            return SliceExecutionResult(
                slice_id=slice_id,
                tool_call_count=tool_call_count,
            )
        except Exception:
            self._finish_slice(
                slice_id,
                execution,
                status=ExecutionStatus.PAUSED_ERROR,
                stop_reason=StopReason.TURN_ERROR.value,
                usage=budget.snapshot(),
            )
            record_trace(
                TraceDirection.INTERNAL,
                TraceLayer.AGENT,
                "agent.slice_finished",
                slice_id=slice_id,
                data={"status": "error", "stop_reason": StopReason.TURN_ERROR.value},
            )
            raise

    def finish_for_goal_continuation(
        self,
        *,
        slice_id: str | None,
        execution,
        graph_steps_used: int,
        usage: dict,
    ) -> None:
        """Close a successful intermediate Slice before a Goal review Slice."""
        self._finish_slice(
            slice_id,
            execution,
            status=ExecutionStatus.COMPLETED,
            stop_reason="goal_continuation",
            graph_steps_used=graph_steps_used,
            usage=usage,
        )

    def _start_slice(self, execution, slice_number: int) -> str | None:
        if execution is None or self.execution_store is None:
            return None
        return self.execution_store.start_slice(
            execution.execution_id,
            execution.grant_index,
            slice_number,
        )

    def _finish_slice(
        self,
        slice_id: str | None,
        execution,
        *,
        status: ExecutionStatus,
        stop_reason: str,
        graph_steps_used: int | None = None,
        usage: dict | None = None,
    ) -> None:
        if slice_id is None or execution is None or self.execution_store is None:
            return
        kwargs = {
            "status": status,
            "stop_reason": stop_reason,
            "usage": usage,
        }
        if graph_steps_used is not None:
            kwargs["graph_steps_used"] = graph_steps_used
        self.execution_store.finish_slice(
            slice_id,
            execution.execution_id,
            **kwargs,
        )
