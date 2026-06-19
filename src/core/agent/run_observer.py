"""Telemetry and trace observer for foreground Agent runs."""

from __future__ import annotations

from src.core.agent.models import StopReason
from src.core.errors import ErrorAction
from src.core.telemetry import emit_event, record_error
from src.core.tracing import TraceDirection, TraceLayer, record_trace


class TurnRunObserver:
    """Publish observability records without polluting the execution loop."""

    def run_started(self, session, user_input: str, run_context, current_turn: int, resume: bool) -> None:
        """Record that the foreground Agent run started."""
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

    def slice_finished(self, slice_id: str) -> None:
        """Record successful completion of one graph Slice."""
        record_trace(
            TraceDirection.INTERNAL,
            TraceLayer.AGENT,
            "agent.slice_finished",
            slice_id=slice_id,
            data={"status": "completed"},
        )

    def run_finished(self, total_tool_calls: int, slice_count: int) -> None:
        """Record successful completion of the foreground Agent run."""
        emit_event(
            "turn_finished",
            "agent_service",
            "Finished workspace Agent turn.",
            {
                "stop_reason": StopReason.COMPLETED.value,
                "tool_call_count": total_tool_calls,
                "slice_count": slice_count,
            },
        )
        record_trace(
            TraceDirection.INTERNAL,
            TraceLayer.AGENT,
            "agent.run_finished",
            data={"status": "ok", "slice_count": slice_count},
        )

    def run_paused(self, summary: str, snapshot: dict, slice_count: int, stop_reason: str) -> None:
        """Record a budget or disconnect pause."""
        emit_event(
            "turn_paused",
            "agent_service",
            summary,
            {"slice_count": slice_count, **snapshot},
        )
        record_trace(
            TraceDirection.INTERNAL,
            TraceLayer.AGENT,
            "agent.run_paused",
            data={"stop_reason": stop_reason, "slice_count": slice_count},
        )

    def run_error_item(self, item: dict) -> None:
        """Record a graph/provider error represented as a stream item."""
        terminated = item["data"].get("error_action") == ErrorAction.TERMINATE
        emit_event(
            "turn_terminated" if terminated else "turn_paused",
            "agent_service",
            (
                "Workspace Agent execution terminated after a non-retryable error."
                if terminated
                else "Workspace Agent execution paused after an error."
            ),
            {
                "stop_reason": item["data"].get(
                    "stop_reason",
                    StopReason.TURN_ERROR.value,
                ),
                "error_type": item["data"].get("type", "unknown"),
                "error_category": item["data"].get("error_category", "unknown"),
            },
            level="error",
        )
        record_trace(
            TraceDirection.INTERNAL,
            TraceLayer.AGENT,
            "agent.run_failed",
            data={"stop_reason": item["data"].get("stop_reason")},
        )

    def unexpected_exception(self, exc: Exception) -> None:
        """Record an unexpected Python exception from the Agent loop."""
        record_error(
            "agent_service",
            "turn",
            exc,
            "Agent turn failed.",
            event_type="turn_failed",
        )
        record_trace(
            TraceDirection.INTERNAL,
            TraceLayer.AGENT,
            "agent.run_failed",
            data={"error_type": type(exc).__name__},
        )
