import unittest

from langchain_core.messages import AIMessage, ToolMessage

from src.core.streaming.events import _step_events_from_message


class StreamingEventsTest(unittest.TestCase):
    def test_agent_message_fallback_keeps_full_content(self):
        content = "answer-" + ("x" * 1200)
        events = _step_events_from_message(AIMessage(content=content))

        self.assertEqual("agent_message", events[0]["data"]["type"])
        self.assertEqual(content, events[0]["data"]["content"])
        self.assertNotIn("... truncated ...", events[0]["data"]["content"])

    def test_tool_result_still_uses_preview_limit(self):
        content = "tool-" + ("x" * 1200)
        events = _step_events_from_message(
            ToolMessage(content=content, tool_call_id="call-1", name="read_file")
        )

        self.assertEqual("tool_call_result", events[0]["data"]["type"])
        self.assertIn("... truncated ...", events[0]["data"]["content"])

    def test_delegate_result_uses_larger_progress_preview(self):
        content = "delegate-" + ("x" * 3000)
        events = _step_events_from_message(
            ToolMessage(content=content, tool_call_id="call-1", name="delegate_to_subagent")
        )

        self.assertEqual(content, events[0]["data"]["content"])

    def test_task_list_result_uses_larger_progress_preview(self):
        content = "tasks-" + ("x" * 3000)
        events = _step_events_from_message(
            ToolMessage(content=content, tool_call_id="call-1", name="task_list")
        )

        self.assertEqual(content, events[0]["data"]["content"])


if __name__ == "__main__":
    unittest.main()
