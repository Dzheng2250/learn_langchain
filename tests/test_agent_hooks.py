import unittest

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph

from agent_hooks import (
    AgentEvent,
    event_span,
    emit_event,
    list_hook_helpers,
    record_command_failed,
    record_command_finished,
    record_command_started,
    record_error,
    record_memory_saved,
    record_tool_failed,
    record_tool_finished,
    record_tool_started,
    sanitize_payload,
    set_event_context,
    set_event_sinks,
)
from agent_observed_tools import ObservedToolNode


class MemorySink:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


class FailingSink:
    def emit(self, event: AgentEvent) -> None:
        raise RuntimeError("sink failed")


class AgentHooksTest(unittest.TestCase):
    def tearDown(self) -> None:
        set_event_sinks(None)
        set_event_context()

    def test_emit_event_writes_to_memory_sink(self) -> None:
        sink = MemorySink()
        set_event_sinks([sink])
        set_event_context(session_id="session-a", turn_index=7, run_id="run-a")

        event = emit_event(
            "turn_started",
            "test",
            "started",
            {"value": 1},
        )

        self.assertEqual(1, len(sink.events))
        self.assertIs(event, sink.events[0])
        self.assertEqual("session-a", event.session_id)
        self.assertEqual(7, event.turn_index)
        self.assertEqual("run-a", event.run_id)

    def test_failing_sink_does_not_interrupt_emit(self) -> None:
        sink = MemorySink()
        set_event_sinks([FailingSink(), sink])

        event = emit_event("tool_started", "test", payload={"tool": "demo"})

        self.assertEqual(1, len(sink.events))
        self.assertEqual("tool_started", event.event_type)

    def test_sensitive_payload_values_are_redacted(self) -> None:
        payload = sanitize_payload(
            {
                "api_key": "secret-value",
                "nested": {"password": "pw", "safe": "ok"},
                "token_value": "abc",
            }
        )

        self.assertEqual("[REDACTED]", payload["api_key"])
        self.assertEqual("[REDACTED]", payload["nested"]["password"])
        self.assertEqual("[REDACTED]", payload["token_value"])
        self.assertEqual("ok", payload["nested"]["safe"])

    def test_long_payload_is_truncated(self) -> None:
        payload = sanitize_payload({"text": "x" * 2000})

        self.assertLess(len(payload["text"]), 1200)
        self.assertIn("event payload truncated", payload["text"])

    def test_event_span_records_started_and_finished(self) -> None:
        sink = MemorySink()
        set_event_sinks([sink])

        with event_span("demo_operation", "test", payload={"value": 1}):
            pass

        self.assertEqual(["demo_operation_started", "demo_operation_finished"], [
            event.event_type for event in sink.events
        ])
        self.assertIsNotNone(sink.events[1].duration_ms)

    def test_event_span_records_failure_and_reraises(self) -> None:
        sink = MemorySink()
        set_event_sinks([sink])

        with self.assertRaises(ValueError):
            with event_span("demo_operation", "test"):
                raise ValueError("bad input")

        self.assertEqual(["demo_operation_started", "demo_operation_failed"], [
            event.event_type for event in sink.events
        ])
        self.assertEqual("error", sink.events[1].level)
        self.assertIn("bad input", sink.events[1].payload["error"])

    def test_record_error_uses_consistent_payload(self) -> None:
        sink = MemorySink()
        set_event_sinks([sink])

        record_error("test", "demo", ValueError("bad"), payload={"safe": "ok"})

        self.assertEqual("demo_failed", sink.events[0].event_type)
        self.assertEqual("error", sink.events[0].level)
        self.assertEqual("demo", sink.events[0].payload["operation"])
        self.assertEqual("ValueError", sink.events[0].payload["error_type"])
        self.assertEqual("bad", sink.events[0].payload["error"])
        self.assertEqual("ok", sink.events[0].payload["safe"])

    def test_record_tool_started_uses_consistent_payload(self) -> None:
        sink = MemorySink()
        set_event_sinks([sink])

        record_tool_started("test", tool="read_file", tool_call_id="call-1", args={"path": "a.py"})

        self.assertEqual("tool_started", sink.events[0].event_type)
        self.assertEqual("read_file", sink.events[0].payload["tool"])
        self.assertEqual("call-1", sink.events[0].payload["tool_call_id"])
        self.assertIn("a.py", sink.events[0].payload["args_preview"])

    def test_record_tool_finished_uses_consistent_payload(self) -> None:
        sink = MemorySink()
        set_event_sinks([sink])

        record_tool_finished("test", tool="read_file", tool_call_id="call-1", content="done")

        self.assertEqual("tool_finished", sink.events[0].event_type)
        self.assertEqual("read_file", sink.events[0].payload["tool"])
        self.assertEqual("call-1", sink.events[0].payload["tool_call_id"])
        self.assertEqual("done", sink.events[0].payload["content_preview"])
        self.assertEqual(4, sink.events[0].payload["content_chars"])

    def test_record_tool_failed_uses_consistent_payload(self) -> None:
        sink = MemorySink()
        set_event_sinks([sink])

        record_tool_failed("test", tool="read_file", tool_call_id="call-1", error=ValueError("bad"))

        self.assertEqual("tool_failed", sink.events[0].event_type)
        self.assertEqual("error", sink.events[0].level)
        self.assertEqual("read_file", sink.events[0].payload["tool"])
        self.assertEqual("call-1", sink.events[0].payload["tool_call_id"])
        self.assertEqual("ValueError", sink.events[0].payload["error_type"])

    def test_record_memory_saved_uses_consistent_payload(self) -> None:
        sink = MemorySink()
        set_event_sinks([sink])

        record_memory_saved(
            "test",
            memory_id="memory-1",
            action="created",
            kind="project_fact",
            importance=5,
            content="important content",
        )

        self.assertEqual("memory_saved", sink.events[0].event_type)
        self.assertEqual("memory-1", sink.events[0].payload["memory_id"])
        self.assertEqual("created", sink.events[0].payload["action"])
        self.assertEqual("project_fact", sink.events[0].payload["kind"])

    def test_record_command_helpers_use_consistent_payload(self) -> None:
        sink = MemorySink()
        set_event_sinks([sink])

        record_command_started("test", "python -V")
        record_command_finished("test", returncode=0, output="Python 3.11")
        record_command_failed("test", reason="nonzero_exit", command="python missing.py", returncode=2)

        self.assertEqual(
            ["command_started", "command_finished", "command_failed"],
            [event.event_type for event in sink.events],
        )
        self.assertIn("python -V", sink.events[0].payload["command_preview"])
        self.assertEqual(0, sink.events[1].payload["returncode"])
        self.assertEqual("nonzero_exit", sink.events[2].payload["reason"])

    def test_hook_helpers_are_registered(self) -> None:
        helpers = list_hook_helpers()

        self.assertIn("record_tool_started", helpers)
        self.assertIn("record_tool_finished", helpers)
        self.assertIn("record_tool_failed", helpers)
        self.assertIn("record_command_started", helpers)
        self.assertIn("record_memory_saved", helpers)

    def test_observed_tool_node_records_tool_boundary_events(self) -> None:
        sink = MemorySink()
        set_event_sinks([sink])

        @tool
        def echo(text: str) -> str:
            """Echo input text."""
            return text

        node = ObservedToolNode([echo])
        builder = StateGraph(MessagesState)
        builder.add_node("tools", node)
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        app = builder.compile()

        output = app.invoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "echo",
                                "args": {"text": "hello"},
                                "id": "call-1",
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            }
        )

        self.assertEqual(["tool_started", "tool_finished"], [event.event_type for event in sink.events])
        self.assertEqual("echo", sink.events[0].payload["tool"])
        self.assertEqual("call-1", sink.events[0].payload["tool_call_id"])
        self.assertEqual("hello", output["messages"][-1].content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
