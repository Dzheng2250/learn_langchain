import unittest

from src.core.errors import (
    AliyunErrorParser,
    ErrorAction,
    ErrorCategory,
    OpenAICompatibleErrorParser,
    ParsedProviderError,
    ProviderErrorHandler,
    ProviderErrorParserRegistry,
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
        self.assertEqual("aliyun", resolution.provider)
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
            [BrokenParser(), OpenAICompatibleErrorParser()]
        )

        parsed = registry.parse(FakeHttpError(429, {"error": {}}))

        self.assertEqual(ErrorCategory.RATE_LIMITED, parsed.category)

    def test_explicit_empty_registry_returns_unknown(self):
        parsed = ProviderErrorParserRegistry([]).parse(FakeHttpError(429, {"error": {}}))

        self.assertEqual(ErrorCategory.UNKNOWN, parsed.category)

    def test_generic_parser_handles_openai_compatible_authentication(self):
        parsed = OpenAICompatibleErrorParser().parse(FakeHttpError(401, {"error": {}}))

        self.assertEqual(ErrorCategory.AUTHENTICATION, parsed.category)

    def test_aliyun_parser_ignores_unrecognized_errors(self):
        self.assertIsNone(AliyunErrorParser().parse(RuntimeError("ordinary failure")))


if __name__ == "__main__":
    unittest.main()
