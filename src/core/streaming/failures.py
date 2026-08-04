"""Failure event helpers for LangGraph stream adaptation."""

from src.core.agent.models import StopReason
from src.core.errors import ErrorCategory, ProviderErrorHandler
from src.core.llm.completion import ModelOutputLimitError
from src.core.telemetry import record_error


def provider_failure_stage(resolution) -> str:
    """Return the foreground stage that best matches a parsed graph exception."""
    if resolution.category != ErrorCategory.UNKNOWN:
        return "parent_model_provider"
    if resolution.provider != "unknown" or resolution.provider_code or resolution.http_status:
        return "parent_model_provider"
    return "parent_graph"


def graph_failure_event(exc: Exception, *, graph_steps_used: int, provider_error_handler=None) -> dict:
    """Record and build one provider/graph failure event."""
    if isinstance(exc, ModelOutputLimitError):
        record_error(
            "agent_stream",
            "llm_output",
            exc,
            "Model output reached its configured token limit.",
            {
                "failure_source": "agent_turn",
                "failure_stage": "parent_model_provider",
                "failure_scope": "current_turn",
                "user_action": "increase_output_limit_and_resume",
            },
            event_type="llm_output_truncated",
        )
        return {
            "event": "error",
            "data": {
                "type": StopReason.MODEL_OUTPUT_LIMIT.value,
                "stop_reason": StopReason.MODEL_OUTPUT_LIMIT.value,
                "message": str(exc),
                "graph_steps_used": graph_steps_used,
                "retryable": True,
                "failure_source": "agent_turn",
                "failure_stage": "parent_model_provider",
                "failure_scope": "current_turn",
                "user_action": "increase_output_limit_and_resume",
            },
        }
    resolution = (provider_error_handler or ProviderErrorHandler()).resolve(exc)
    failure_context = {
        "cause_type": type(exc).__name__,
        "failure_source": "agent_turn",
        "failure_stage": provider_failure_stage(resolution),
        "failure_scope": "current_turn",
        "user_action": (
            "revise_input_and_retry"
            if resolution.action.value == "terminate"
            else "resume_later"
        ),
    }
    record_error(
        "agent_stream",
        "llm_or_graph",
        RuntimeError(resolution.public_message),
        "Graph execution failed.",
        {**resolution.event_data(), **failure_context},
        event_type="llm_or_graph_failed",
    )
    return {
        "event": "error",
        "data": {
            "type": "provider_error",
            "stop_reason": StopReason.GRAPH_ERROR.value,
            "message": resolution.public_message,
            "graph_steps_used": graph_steps_used,
            **resolution.event_data(),
            **failure_context,
        },
    }
