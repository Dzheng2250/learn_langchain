import unittest

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from src.core.common.content import content_text, reasoning_content_text
from src.core.streaming.events import stream_graph_events
from src.core.streaming.message_events import step_events_from_message, tool_calls_from_message
class FakeGraph:
    def __init__(self, chunks):
        self.chunks = chunks

    def stream(self, _inputs, **_kwargs):
        yield from self.chunks


class FailingGraph(FakeGraph):
    def stream(self, _inputs, **_kwargs):
        yield from self.chunks
        raise RuntimeError("boom")


class StreamingEventsTest(unittest.TestCase):
    def test_agent_message_fallback_keeps_full_content(self):
        content = "answer-" + ("x" * 1200)
        events = step_events_from_message(AIMessage(content=content))

        self.assertEqual("agent_message", events[0]["data"]["type"])
        self.assertEqual(content, events[0]["data"]["content"])
        self.assertNotIn("... truncated ...", events[0]["data"]["content"])


    def test_agent_message_extracts_text_from_content_blocks(self):
        events = step_events_from_message(
            AIMessage(content=[{"type": "text", "text": "hi"}])
        )

        self.assertEqual("agent_message", events[0]["data"]["type"])
        self.assertEqual("hi", events[0]["data"]["content"])

    def test_anthropic_tool_use_blocks_are_step_events(self):
        events = step_events_from_message(
            AIMessage(
                content=[
                    {"type": "thinking", "thinking": "hidden"},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "read_file",
                        "input": {"path": "README.md"},
                    },
                ]
            )
        )

        self.assertEqual("tool_call_start", events[0]["data"]["type"])
        self.assertEqual("read_file", events[0]["data"]["tool"])
        self.assertEqual({"path": "README.md"}, events[0]["data"]["args"])
        self.assertEqual("toolu_1", events[0]["data"]["id"])

    def test_tool_call_block_alias_is_normalized(self):
        calls = tool_calls_from_message(
            AIMessage(
                content=[
                    {
                        "type": "tool_call",
                        "id": "call-1",
                        "name": "get_weather",
                        "args": {"city": "北京"},
                    }
                ]
            )
        )

        self.assertEqual(
            [{"name": "get_weather", "args": {"city": "北京"}, "id": "call-1"}],
            calls,
        )

    def test_tool_result_still_uses_preview_limit(self):
        content = "tool-" + ("x" * 1200)
        events = step_events_from_message(
            ToolMessage(content=content, tool_call_id="call-1", name="read_file")
        )

        self.assertEqual("tool_call_result", events[0]["data"]["type"])
        self.assertIn("... truncated ...", events[0]["data"]["content"])

    def test_delegate_result_uses_larger_progress_preview(self):
        content = "delegate-" + ("x" * 3000)
        events = step_events_from_message(
            ToolMessage(content=content, tool_call_id="call-1", name="delegate_to_subagent")
        )

        self.assertEqual(content, events[0]["data"]["content"])

    def test_task_list_result_uses_larger_progress_preview(self):
        content = "tasks-" + ("x" * 3000)
        events = step_events_from_message(
            ToolMessage(content=content, tool_call_id="call-1", name="task_list")
        )

        self.assertEqual(content, events[0]["data"]["content"])



    def test_content_text_still_filters_reasoning_blocks(self):
        content = [
            {"type": "thinking", "thinking": "hidden"},
            {"type": "text", "text": "visible"},
        ]

        self.assertEqual("visible", content_text(content))
        self.assertEqual(("hidden", False, 6), reasoning_content_text(content))

    def test_stream_graph_events_emits_reasoning_separately_from_tokens(self):
        graph = FakeGraph(
            [
                (
                    "messages",
                    (
                        AIMessageChunk(
                            content=[{"type": "thinking", "thinking": "hidden"}]
                        ),
                        {},
                    ),
                ),
                (
                    "messages",
                    (AIMessageChunk(content=[{"type": "text", "text": "answer"}]), {}),
                ),
                ("values", {"messages": []}),
            ]
        )

        events = list(stream_graph_events(graph, []))
        event_names = [item["event"] for item in events]

        self.assertIn("reasoning_started", event_names)
        self.assertIn("reasoning_finished", event_names)
        self.assertIn({"event": "token", "data": {"content": "answer"}}, events)
        token_payload = next(item["data"] for item in events if item["event"] == "token")
        self.assertNotIn("hidden", token_payload["content"])

    def test_reasoning_finished_is_emitted_when_stream_errors(self):
        graph = FailingGraph(
            [
                (
                    "messages",
                    (
                        AIMessageChunk(
                            content=[{"type": "thinking", "thinking": "before failure"}]
                        ),
                        {},
                    ),
                ),
            ]
        )

        events = list(stream_graph_events(graph, []))
        event_names = [item["event"] for item in events]

        self.assertIn("reasoning_started", event_names)
        self.assertIn("reasoning_finished", event_names)
        self.assertIn("error", event_names)
        self.assertLess(
            event_names.index("error"),
            event_names.index("reasoning_finished"),
        )

    def test_redacted_thinking_does_not_expose_content(self):
        events = list(
            stream_graph_events(
                FakeGraph(
                    [
                        (
                            "messages",
                            (
                                AIMessageChunk(
                                    content=[{"type": "redacted_thinking", "data": "secret"}]
                                ),
                                {},
                            ),
                        ),
                        ("values", {"messages": []}),
                    ]
                ),
                [],
            )
        )

        deltas = [item for item in events if item["event"] == "reasoning_delta"]
        self.assertEqual([], deltas)
        finished = [item for item in events if item["event"] == "reasoning_finished"]
        self.assertTrue(finished)
        self.assertTrue(finished[-1]["data"]["redacted"])
        self.assertNotIn("secret", str(events))
if __name__ == "__main__":
    unittest.main()
