"""Tenacity-backed model decorator with provider-neutral recovery policy."""

from datetime import datetime, timezone
import random
from uuid import uuid4

from langchain_core.runnables import Runnable
from tenacity import Retrying, retry_if_exception, stop_after_attempt
from tenacity.wait import wait_base

from src.config.llm import LlmRetrySettings
from src.core.errors import ProviderErrorHandler
from src.core.llm.contracts import LlmPurpose, ModelProvider
from src.core.llm.retry_context import (
    bind_attempt,
    current_attempt_has_output,
    emit_retry_event,
    reset_attempt,
)
from src.core.telemetry import emit_event
from src.core.tracing.models import TraceDirection, TraceLayer
from src.core.tracing.recorder import record_trace

_FOREGROUND_PURPOSES = frozenset(
    {LlmPurpose.PARENT_AGENT, LlmPurpose.SUBAGENT, LlmPurpose.FILE_SUMMARY}
)


class ProviderAwareWait(wait_base):
    """Prefer provider retry hints, then use capped exponential jitter."""

    def __init__(self, error_handler, settings, *, random_source=None) -> None:
        self.error_handler = error_handler
        self.settings = settings
        self.random_source = random_source or random.uniform

    def __call__(self, retry_state) -> float:
        resolution = self.error_handler.resolve(retry_state.outcome.exception())
        hinted = resolution.retry_after_seconds
        if hinted is None and resolution.retry_at is not None:
            hinted = max(
                0.0,
                (resolution.retry_at - datetime.now(timezone.utc)).total_seconds(),
            )
        if hinted is not None:
            return min(self.settings.max_delay_seconds, max(0.0, hinted))
        exponent = max(0, retry_state.attempt_number - 1)
        base = min(
            self.settings.max_delay_seconds,
            self.settings.base_delay_seconds * (2**exponent),
        )
        spread = base * self.settings.jitter_ratio
        return min(
            self.settings.max_delay_seconds,
            max(0.0, self.random_source(base - spread, base + spread)),
        )


class RetryingRunnable(Runnable):
    """Retry one model invocation without replaying graph or tool nodes."""

    def __init__(self, inner, *, purpose, settings, error_handler) -> None:
        self.inner = inner
        self.purpose = purpose
        self.settings = settings
        self.error_handler = error_handler
        self.max_attempts = (
            settings.foreground_max_attempts
            if purpose in _FOREGROUND_PURPOSES
            else settings.background_max_attempts
        )

    def invoke(self, input, config=None, **kwargs):
        if not self.settings.enabled or self.max_attempts == 1:
            return self.inner.invoke(input, config=config, **kwargs)
        retrying = Retrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=ProviderAwareWait(self.error_handler, self.settings),
            retry=retry_if_exception(
                lambda exc: self.error_handler.resolve(exc).retryable
            ),
            before_sleep=self._before_sleep,
            reraise=True,
        )
        attempt_number = 0
        try:
            for attempt in retrying:
                attempt_number = attempt.retry_state.attempt_number
                with attempt:
                    return self._invoke_attempt(
                        attempt.retry_state.attempt_number,
                        input,
                        config,
                        kwargs,
                    )
        except Exception as exc:
            resolution = self.error_handler.resolve(exc)
            if not resolution.retryable:
                raise
            data = {
                "attempt": attempt_number,
                "max_attempts": self.max_attempts,
                **resolution.event_data(),
            }
            self._record(
                "llm_retry_exhausted", "llm.retry_exhausted", data, level="error"
            )
            emit_retry_event(
                "model_retry_exhausted",
                {"purpose": self.purpose.value, **data},
            )
            raise

    def _invoke_attempt(self, attempt, input, config, kwargs):
        attempt_id = uuid4().hex
        token = bind_attempt(attempt_id)
        self._record(
            "llm_attempt_started",
            "llm.attempt_started",
            {
                "attempt_id": attempt_id,
                "attempt": attempt,
                "max_attempts": self.max_attempts,
            },
        )
        try:
            return self.inner.invoke(input, config=config, **kwargs)
        except Exception as exc:
            resolution = self.error_handler.resolve(exc)
            data = {
                "attempt_id": attempt_id,
                "attempt": attempt,
                "max_attempts": self.max_attempts,
                "output_emitted": current_attempt_has_output(),
                **resolution.event_data(),
            }
            self._record(
                "llm_attempt_failed",
                "llm.attempt_failed",
                data,
                level="warning" if resolution.retryable else "error",
            )
            if data["output_emitted"] and resolution.retryable:
                emit_retry_event(
                    "model_attempt_invalidated",
                    {"purpose": self.purpose.value, **data},
                )
            raise
        finally:
            reset_attempt(token)

    def _before_sleep(self, retry_state) -> None:
        resolution = self.error_handler.resolve(retry_state.outcome.exception())
        data = {
            "purpose": self.purpose.value,
            "attempt": retry_state.attempt_number,
            "next_attempt": retry_state.attempt_number + 1,
            "max_attempts": self.max_attempts,
            "delay_seconds": float(retry_state.next_action.sleep),
            **resolution.event_data(),
        }
        self._record("llm_retry_scheduled", "llm.retry_scheduled", data)
        emit_retry_event("model_retry_scheduled", data)

    def _record(self, event_type, trace_kind, data, *, level="info") -> None:
        payload = {"purpose": self.purpose.value, **data}
        emit_event(event_type, "llm_resilience", trace_kind, payload, level=level)
        record_trace(
            TraceDirection.INTERNAL,
            TraceLayer.LLM,
            trace_kind,
            data=payload,
        )


class ResilientModelProvider:
    """Decorate every model purpose with shared parsing and retry policy."""

    def __init__(
        self,
        inner: ModelProvider,
        *,
        settings: LlmRetrySettings,
        error_handler: ProviderErrorHandler,
    ) -> None:
        self.inner = inner
        self.settings = settings
        self.error_handler = error_handler

    def configuration_status(self):
        return self.inner.configuration_status()

    def create_chat_model(self, purpose, **kwargs):
        return RetryingRunnable(
            self.inner.create_chat_model(purpose, **kwargs),
            purpose=purpose,
            settings=self.settings,
            error_handler=self.error_handler,
        )
