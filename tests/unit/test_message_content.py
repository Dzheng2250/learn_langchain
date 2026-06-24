import unittest

from langchain_core.messages import AIMessage

from src.core.common.content import message_content_text


class MessageContentTextTest(unittest.TestCase):
    def test_plain_string_content_is_returned(self):
        self.assertEqual("hello", message_content_text("hello"))

    def test_anthropic_text_blocks_are_joined(self):
        message = AIMessage(
            content=[
                {"type": "text", "text": "hello"},
                {"type": "text", "text": " world"},
            ]
        )

        self.assertEqual("hello world", message_content_text(message))

    def test_tool_and_reasoning_blocks_are_not_display_text(self):
        content = [
            {"type": "thinking", "thinking": "hidden"},
            {"type": "tool_use", "id": "toolu_1", "name": "read_file"},
        ]

        self.assertEqual("", message_content_text(content))

    def test_anthropic_tool_argument_deltas_are_not_display_text(self):
        content = [
            {"type": "text", "text": "checking"},
            {"type": "input_json_delta", "partial_json": '{"city": "Kunming"}'},
        ]

        self.assertEqual("checking", message_content_text(content))

    def test_anthropic_non_text_deltas_are_ignored(self):
        content = [
            {"type": "input_json_delta", "partial_json": '{"city": "Kunming"}'},
            {"type": "thinking_delta", "thinking": "hidden"},
            {"type": "signature_delta", "signature": "hidden"},
        ]

        self.assertEqual("", message_content_text(content))

    def test_unknown_blocks_fall_back_to_json(self):
        self.assertEqual(
            '{"type": "custom", "value": 3}',
            message_content_text({"type": "custom", "value": 3}),
        )


if __name__ == "__main__":
    unittest.main()
