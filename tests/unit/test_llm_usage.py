import unittest

from langchain_core.messages import AIMessage

from src.core.llm.usage import context_tokens, has_context_usage, message_usage


class LlmUsageTest(unittest.TestCase):
    def test_context_tokens_are_exact_input_plus_output(self):
        usage = message_usage(
            AIMessage(
                content="answer",
                usage_metadata={
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "total_tokens": 999,
                },
            )
        )

        self.assertEqual(120, usage["input_tokens"])
        self.assertEqual(30, usage["output_tokens"])
        self.assertEqual(150, usage["total_tokens"])
        self.assertEqual(150, context_tokens(usage))

    def test_provider_total_is_used_only_when_components_are_unavailable(self):
        usage = message_usage(
            AIMessage(
                content="answer",
                response_metadata={"usage": {"total_tokens": 42}},
            )
        )

        self.assertEqual(42, context_tokens(usage))

    def test_prompt_and_completion_aliases_are_normalized(self):
        usage = message_usage(
            AIMessage(
                content="answer",
                response_metadata={
                    "token_usage": {"prompt_tokens": 11, "completion_tokens": 7}
                },
            )
        )

        self.assertEqual(18, context_tokens(usage))

    def test_missing_usage_is_not_reported_as_zero(self):
        usage = message_usage(AIMessage(content="answer"))

        self.assertFalse(has_context_usage(usage))
        self.assertEqual(0, context_tokens(usage))


if __name__ == "__main__":
    unittest.main()
