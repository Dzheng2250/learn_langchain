import unittest
from types import SimpleNamespace

from langchain_core.runnables import Runnable

from src.config.llm import LlmRetrySettings
from src.core.errors import ProviderErrorHandler
from src.core.llm.contracts import LlmPurpose
from src.core.llm.resilience import ProviderAwareWait, RetryingRunnable
from src.core.llm.retry_context import (
    bind_retry_event_callback,
    mark_attempt_output_emitted,
    reset_retry_event_callback,
)


class FakeHttpError(Exception):
    def __init__(self, status_code, body=None, headers=None):
        super().__init__(f"HTTP status: {status_code}")
        self.status_code = status_code
        self.body = body or {}
        self.headers = headers or {}


class SequenceRunnable(Runnable):
    def __init__(self, outcomes, *, emit_partial=False):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.emit_partial = emit_partial

    def invoke(self, input, config=None, **kwargs):
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            if self.emit_partial:
                mark_attempt_output_emitted()
            raise outcome
        return outcome


def settings(*, foreground=3, background=2):
    return LlmRetrySettings(
        foreground_max_attempts=foreground,
        background_max_attempts=background,
        base_delay_seconds=0,
        max_delay_seconds=0,
        jitter_ratio=0,
    )


class LlmResilienceTest(unittest.TestCase):
    def test_transient_failure_retries_only_model_invocation(self):
        inner = SequenceRunnable([FakeHttpError(503), "ok"])
        events = []
        token = bind_retry_event_callback(events.append)
        try:
            result = RetryingRunnable(
                inner,
                purpose=LlmPurpose.PARENT_AGENT,
                settings=settings(),
                error_handler=ProviderErrorHandler(),
            ).invoke("input")
        finally:
            reset_retry_event_callback(token)

        self.assertEqual("ok", result)
        self.assertEqual(2, inner.calls)
        self.assertEqual(["model_retry_scheduled"], [e["event"] for e in events])

    def test_permanent_failure_is_not_retried(self):
        inner = SequenceRunnable([FakeHttpError(422)])
        events = []
        token = bind_retry_event_callback(events.append)
        try:
            with self.assertRaises(FakeHttpError):
                RetryingRunnable(
                    inner,
                    purpose=LlmPurpose.PARENT_AGENT,
                    settings=settings(),
                    error_handler=ProviderErrorHandler(),
                ).invoke("input")
        finally:
            reset_retry_event_callback(token)

        self.assertEqual(1, inner.calls)
        self.assertEqual([], events)

    def test_partial_attempt_is_marked_stale_before_retry(self):
        inner = SequenceRunnable([FakeHttpError(503), "ok"], emit_partial=True)
        events = []
        token = bind_retry_event_callback(events.append)
        try:
            RetryingRunnable(
                inner,
                purpose=LlmPurpose.PARENT_AGENT,
                settings=settings(),
                error_handler=ProviderErrorHandler(),
            ).invoke("input")
        finally:
            reset_retry_event_callback(token)

        self.assertEqual(
            ["model_attempt_invalidated", "model_retry_scheduled"],
            [e["event"] for e in events],
        )
        self.assertTrue(events[0]["data"]["attempt_id"])

    def test_background_uses_smaller_attempt_budget(self):
        inner = SequenceRunnable([FakeHttpError(503), FakeHttpError(503)])
        with self.assertRaises(FakeHttpError):
            RetryingRunnable(
                inner,
                purpose=LlmPurpose.MEMORY_EXTRACTION,
                settings=settings(foreground=3, background=2),
                error_handler=ProviderErrorHandler(),
            ).invoke("input")
        self.assertEqual(2, inner.calls)

    def test_provider_retry_hint_precedes_local_backoff(self):
        error = FakeHttpError(429, headers={"Retry-After": "7"})
        retry_state = SimpleNamespace(
            attempt_number=1,
            outcome=SimpleNamespace(exception=lambda: error),
        )
        wait = ProviderAwareWait(
            ProviderErrorHandler(),
            LlmRetrySettings(max_delay_seconds=30),
            random_source=lambda low, high: low,
        )
        self.assertEqual(7, wait(retry_state))


if __name__ == "__main__":
    unittest.main()
