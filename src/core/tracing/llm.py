"""Provider decorator that attaches service-independent LangChain trace callbacks."""

import time
from threading import Lock
from langchain_core.callbacks import BaseCallbackHandler

from src.core.llm.contracts import LlmPurpose, ModelProvider
from src.core.llm.usage import has_context_usage, response_usage
from src.core.tracing.models import TraceDirection, TraceLayer
from src.core.tracing.recorder import record_trace


class LlmTraceCallback(BaseCallbackHandler):
    """Record one summary for each LangChain chat-model invocation."""

    def __init__(self, *, purpose: str, model: str = "") -> None:
        self.purpose = purpose
        self.model = model
        self._started: dict[str, tuple[int, str]] = {}
        self._lock = Lock()

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs) -> None:
        call_id = str(run_id)
        record = record_trace(
            TraceDirection.CORE_TO_PROVIDER,
            TraceLayer.LLM,
            "llm.request_started",
            span_id=call_id,
            data={
                "purpose": self.purpose,
                "model": self.model or serialized.get("name", ""),
                "batch_count": len(messages),
                "message_count": sum(len(batch) for batch in messages),
                "tool_count": len((kwargs.get("invocation_params") or {}).get("tools") or []),
            },
        )
        with self._lock:
            self._started[call_id] = (time.monotonic_ns(), getattr(record, "trace_id", ""))

    def on_llm_end(self, response, *, run_id, **kwargs) -> None:
        call_id = str(run_id)
        with self._lock:
            started = self._started.pop(call_id, None)
        duration_ms = int((time.monotonic_ns() - started[0]) / 1_000_000) if started else None
        usage, stop_reason = _response_summary(response)
        record_trace(
            TraceDirection.PROVIDER_TO_CORE,
            TraceLayer.LLM,
            "llm.response_finished",
            trace_id=started[1] if started and started[1] else None,
            span_id=call_id,
            duration_ms=duration_ms,
            data={"purpose": self.purpose, "model": self.model, "stop_reason": stop_reason, **usage},
        )
        # Update the ExecutionBudget with token counts from this LLM call
        try:
            from src.core.agent.budget import current_execution_budget

            budget = current_execution_budget()
            if (
                budget is not None
                and self.purpose == LlmPurpose.PARENT_AGENT.value
                and has_context_usage(usage)
            ):
                input_tokens = usage.get("input_tokens") or 0
                output_tokens = usage.get("output_tokens") or 0
                with budget._lock:
                    budget.input_tokens = input_tokens
                    budget.output_tokens = output_tokens
                    budget.usage_reported = True
        except Exception:
            pass

    def on_llm_error(self, error, *, run_id, **kwargs) -> None:
        call_id = str(run_id)
        with self._lock:
            started = self._started.pop(call_id, None)
        duration_ms = int((time.monotonic_ns() - started[0]) / 1_000_000) if started else None
        record_trace(
            TraceDirection.PROVIDER_TO_CORE,
            TraceLayer.LLM,
            "llm.request_failed",
            trace_id=started[1] if started and started[1] else None,
            span_id=call_id,
            duration_ms=duration_ms,
            data={"purpose": self.purpose, "model": self.model, "error_type": type(error).__name__},
        )


class TracingModelProvider:
    """Decorate any ModelProvider without coupling tracing to its implementation."""

    def __init__(self, inner: ModelProvider) -> None:
        self.inner = inner

    def configuration_status(self):
        return self.inner.configuration_status()

    def create_chat_model(self, purpose, **kwargs):
        model = self.inner.create_chat_model(purpose, **kwargs)
        model_name = str(getattr(self.inner, "model", ""))
        callback = LlmTraceCallback(purpose=purpose.value, model=model_name)
        return model.with_config(callbacks=[callback], metadata={"trace_purpose": purpose.value})


def _response_summary(response) -> tuple[dict, str | None]:
    usage = response_usage(response)
    stop_reason = None
    generations = getattr(response, "generations", None) or []
    if generations and generations[0]:
        message = getattr(generations[0][0], "message", None)
        metadata = getattr(message, "response_metadata", None) or {}
        stop_reason = metadata.get("stop_reason") or metadata.get("finish_reason")
    return usage, stop_reason
