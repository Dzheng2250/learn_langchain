import unittest

from langchain_core.messages import AIMessage, HumanMessage

from src.core.memory.policy import has_explicit_memory_request, should_extract_long_term_memory


class MemoryExtractionPolicyTest(unittest.TestCase):
    """Tests for deciding whether to spend an LLM call on long-term memory extraction."""

    def test_explicit_memory_hint_triggers_extraction(self) -> None:
        self.assertTrue(has_explicit_memory_request("请记住我喜欢中文注释"))
        self.assertTrue(
            should_extract_long_term_memory(
                "请记住我喜欢中文注释",
                turn_index=1,
                turn_messages=[HumanMessage(content="short"), AIMessage(content="ok")],
            )
        )

    def test_interval_turn_triggers_extraction(self) -> None:
        self.assertTrue(
            should_extract_long_term_memory(
                "普通问题",
                turn_index=5,
                turn_messages=[HumanMessage(content="short"), AIMessage(content="ok")],
            )
        )

    def test_large_turn_triggers_extraction(self) -> None:
        self.assertTrue(
            should_extract_long_term_memory(
                "普通问题",
                turn_index=1,
                turn_messages=[
                    HumanMessage(content="short"),
                    AIMessage(content="x" * 1300),
                ],
            )
        )

    def test_small_ordinary_turn_does_not_trigger_extraction(self) -> None:
        self.assertFalse(
            should_extract_long_term_memory(
                "普通问题",
                turn_index=1,
                turn_messages=[HumanMessage(content="short"), AIMessage(content="ok")],
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
