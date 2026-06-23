import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from src.core.errors import (
    AliyunErrorParser,
    AnthropicErrorParser,
    ErrorAction,
    ErrorCategory,
    ParsedProviderError,
    ProviderErrorHandler,
    ProviderErrorParserRegistry,
    GenericProviderErrorParser,
)


class FakeHttpError(Exception):
    def __init__(self, status_code, body=None):
        super().__init__(f"HTTP status: {status_code}")
        self.status_code = status_code
        self.body = body


class ProviderErrorParserTest(unittest.TestCase):
    def test_aliyun_content_rejection_is_terminal_and_sanitized(self):
        error = FakeHttpError(
            400,
            {
                "error": {
                    "message": "sensitive provider detail",
                    "type": "data_inspection_failed",
                    "code": "data_inspection_failed",
                }
            },
        )

        resolution = ProviderErrorHandler().resolve(error)

        self.assertEqual(ErrorCategory.CONTENT_REJECTED, resolution.category)
        self.assertEqual(ErrorAction.TERMINATE, resolution.action)
        self.assertFalse(resolution.retryable)
        self.assertEqual("anthropic", resolution.provider)
        self.assertNotIn("sensitive provider detail", resolution.public_message)

    def test_text_only_aliyun_error_is_recognized(self):
        error = RuntimeError(
            "Error code: 400 - {'error': {'code': 'data_inspection_failed'}}"
        )

        resolution = ProviderErrorHandler().resolve(error)

        self.assertEqual(ErrorCategory.CONTENT_REJECTED, resolution.category)
        self.assertEqual(400, resolution.http_status)

    def test_rate_limit_is_paused_for_retry(self):
        resolution = ProviderErrorHandler().resolve(FakeHttpError(429, {"error": {}}))

        self.assertEqual(ErrorCategory.RATE_LIMITED, resolution.category)
        self.assertEqual(ErrorAction.PAUSE, resolution.action)
        self.assertTrue(resolution.retryable)

    def test_registry_supports_new_provider_parser_without_policy_changes(self):
        class CustomParser:
            def parse(self, exc):
                return ParsedProviderError(
                    ErrorCategory.SERVICE_UNAVAILABLE,
                    provider="custom",
                    provider_code="maintenance",
                    http_status=503,
                )

        handler = ProviderErrorHandler(ProviderErrorParserRegistry([CustomParser()]))
        resolution = handler.resolve(RuntimeError("vendor-specific payload"))

        self.assertEqual("custom", resolution.provider)
        self.assertEqual(ErrorAction.PAUSE, resolution.action)

    def test_broken_vendor_parser_does_not_replace_original_error(self):
        class BrokenParser:
            def parse(self, _exc):
                raise ValueError("parser bug")

        registry = ProviderErrorParserRegistry(
            [BrokenParser(), GenericProviderErrorParser()]
        )

        parsed = registry.parse(FakeHttpError(429, {"error": {}}))

        self.assertEqual(ErrorCategory.RATE_LIMITED, parsed.category)

    def test_explicit_empty_registry_returns_unknown(self):
        parsed = ProviderErrorParserRegistry([]).parse(FakeHttpError(429, {"error": {}}))

        self.assertEqual(ErrorCategory.UNKNOWN, parsed.category)

    def test_generic_parser_handles_http_authentication(self):
        parsed = GenericProviderErrorParser().parse(FakeHttpError(401, {"error": {}}))

        self.assertEqual(ErrorCategory.AUTHENTICATION, parsed.category)

    def test_aliyun_parser_ignores_unrecognized_errors(self):
        self.assertIsNone(AliyunErrorParser().parse(RuntimeError("ordinary failure")))


    def test_anthropic_overloaded_error_is_retryable(self):
        parsed = AnthropicErrorParser().parse(
            FakeHttpError(529, {"error": {"type": "overloaded_error"}})
        )

        self.assertEqual(ErrorCategory.SERVICE_OVERLOADED, parsed.category)
        self.assertEqual("anthropic", parsed.provider)
        self.assertTrue(parsed.retryable_hint)

    def test_anthropic_invalid_request_and_model_not_found_are_terminal(self):
        cases = (
            (400, "invalid_request_error", ErrorCategory.INVALID_REQUEST),
            (404, "not_found_error", ErrorCategory.MODEL_NOT_FOUND),
            (401, "authentication_error", ErrorCategory.AUTHENTICATION),
        )
        for status, code, category in cases:
            with self.subTest(code=code):
                parsed = AnthropicErrorParser().parse(
                    FakeHttpError(status, {"error": {"type": code}})
                )
                self.assertEqual(category, parsed.category)
                self.assertEqual("anthropic", parsed.provider)
                self.assertFalse(parsed.retryable_hint)
    def test_retry_after_header_and_request_id_are_preserved(self):
        error = FakeHttpError(429, {"error": {"type": "rate_limit_error"}})
        error.response = SimpleNamespace(
            status_code=429,
            headers={"Retry-After": "1.5", "x-request-id": "request-1"},
            json=lambda: error.body,
        )

        parsed = GenericProviderErrorParser().parse(error)

        self.assertEqual(ErrorCategory.RATE_LIMITED, parsed.category)
        self.assertEqual(1.5, parsed.retry_after_seconds)
        self.assertEqual("request-1", parsed.request_id)

    def test_text_retry_hint_and_nested_exception_are_parsed(self):
        inner = RuntimeError("Rate limit exceeded. Please try again in 250ms.")
        inner.status_code = 429
        outer = RuntimeError("model invocation failed")
        outer.__cause__ = inner

        parsed = GenericProviderErrorParser().parse(outer)

        self.assertEqual(ErrorCategory.RATE_LIMITED, parsed.category)
        self.assertAlmostEqual(0.25, parsed.retry_after_seconds)

    def test_usage_quota_and_validation_errors_are_not_retryable(self):
        cases = (
            (429, "usage_limit_reached", ErrorCategory.USAGE_LIMIT),
            (429, "insufficient_quota", ErrorCategory.QUOTA_EXHAUSTED),
            (422, "validation_error", ErrorCategory.INVALID_REQUEST),
        )
        for status, code, category in cases:
            with self.subTest(code=code):
                resolution = ProviderErrorHandler().resolve(
                    FakeHttpError(status, {"error": {"code": code}})
                )
                self.assertEqual(category, resolution.category)
                self.assertFalse(resolution.retryable)

    def test_context_and_model_errors_are_classified(self):
        context = ProviderErrorHandler().resolve(
            FakeHttpError(400, {"error": {"code": "context_length_exceeded"}})
        )
        missing = ProviderErrorHandler().resolve(
            FakeHttpError(404, {"error": {"code": "model_not_found"}})
        )
        self.assertEqual(ErrorCategory.CONTEXT_LENGTH_EXCEEDED, context.category)
        self.assertEqual(ErrorCategory.MODEL_NOT_FOUND, missing.category)


if __name__ == "__main__":
    unittest.main()
