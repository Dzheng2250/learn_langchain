import unittest
from uuid import uuid4

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph

from src.core.telemetry import (
    BaseEventSink,
    BufferedEventSink,
    EventBus,
    TelemetryEvent,
    bind_context,
    event_span,
    emit_event,
    install_event_bus,
    record_command_failed,
    record_command_finished,
    record_command_started,
    record_error,
    record_memory_saved,
    record_tool_failed,
    record_tool_finished,
    record_tool_started,
    sanitize_payload,
)
from src.core.tools.observed import ObservedToolNode


class MemorySink(BaseEventSink):
    def __init__(self) -> None:
        self.events = []

    def emit(self, event: TelemetryEvent) -> None:
        self.events.append(event)


class FailingSink(BaseEventSink):
    def emit(self, event: TelemetryEvent) -> None:
        raise RuntimeError("sink failed")


class TelemetryTest(unittest.TestCase):
    def tearDown(self) -> None:
        install_event_bus(None)
        bind_context()

    def test_emit_event_writes_to_memory_sink(self) -> None:
        sink = MemorySink()
        install_event_bus(EventBus([sink]))
        workspace_id = uuid4()
        session_id = uuid4()
        bind_context(
            workspace_id=workspace_id,
            session_id=session_id,
            turn_index=7,
            run_id="run-a",
        )

        event = emit_event(
            "turn_started",
            "test",
            "started",
            {"value": 1},
        )

        self.assertEqual(1, len(sink.events))
        self.assertIs(event, sink.events[0])
        self.assertEqual(workspace_id, event.workspace_id)
        self.assertEqual(session_id, event.session_id)
        self.assertEqual(7, event.turn_index)
        self.assertEqual("run-a", event.run_id)

    def test_legacy_hooks_imports_remain_available(self) -> None:
        from src.core.hooks import AgentEvent, set_event_context

        self.assertIs(AgentEvent, TelemetryEvent)
        self.assertIs(set_event_context, bind_context)

    def test_failing_sink_does_not_interrupt_emit(self) -> None:
        sink = MemorySink()
        install_event_bus(EventBus([FailingSink(), sink]))

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
        install_event_bus(EventBus([sink]))

        with event_span("demo_operation", "test", payload={"value": 1}):
            pass

        self.assertEqual(["demo_operation_started", "demo_operation_finished"], [
            event.event_type for event in sink.events
        ])
        self.assertIsNotNone(sink.events[1].duration_ms)

    def test_event_span_records_failure_and_reraises(self) -> None:
        sink = MemorySink()
        install_event_bus(EventBus([sink]))

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
        install_event_bus(EventBus([sink]))

        record_error("test", "demo", ValueError("bad"), payload={"safe": "ok"})

        self.assertEqual("demo_failed", sink.events[0].event_type)
        self.assertEqual("error", sink.events[0].level)
        self.assertEqual("demo", sink.events[0].payload["operation"])
        self.assertEqual("ValueError", sink.events[0].payload["error_type"])
        self.assertEqual("bad", sink.events[0].payload["error"])
        self.assertEqual("ok", sink.events[0].payload["safe"])

    def test_record_tool_started_uses_consistent_payload(self) -> None:
        sink = MemorySink()
        install_event_bus(EventBus([sink]))

        record_tool_started("test", tool="read_file", tool_call_id="call-1", args={"path": "a.py"})

        self.assertEqual("tool_started", sink.events[0].event_type)
        self.assertEqual("read_file", sink.events[0].payload["tool"])
        self.assertEqual("call-1", sink.events[0].payload["tool_call_id"])
        self.assertIn("a.py", sink.events[0].payload["args_preview"])

    def test_record_tool_finished_uses_consistent_payload(self) -> None:
        sink = MemorySink()
        install_event_bus(EventBus([sink]))

        record_tool_finished("test", tool="read_file", tool_call_id="call-1", content="done")

        self.assertEqual("tool_finished", sink.events[0].event_type)
        self.assertEqual("read_file", sink.events[0].payload["tool"])
        self.assertEqual("call-1", sink.events[0].payload["tool_call_id"])
        self.assertEqual("done", sink.events[0].payload["content_preview"])
        self.assertEqual(4, sink.events[0].payload["content_chars"])

    def test_record_tool_failed_uses_consistent_payload(self) -> None:
        sink = MemorySink()
        install_event_bus(EventBus([sink]))

        record_tool_failed("test", tool="read_file", tool_call_id="call-1", error=ValueError("bad"))

        self.assertEqual("tool_failed", sink.events[0].event_type)
        self.assertEqual("error", sink.events[0].level)
        self.assertEqual("read_file", sink.events[0].payload["tool"])
        self.assertEqual("call-1", sink.events[0].payload["tool_call_id"])
        self.assertEqual("ValueError", sink.events[0].payload["error_type"])

    def test_record_memory_saved_uses_consistent_payload(self) -> None:
        sink = MemorySink()
        install_event_bus(EventBus([sink]))

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
        install_event_bus(EventBus([sink]))

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

    def test_observed_tool_node_records_tool_boundary_events(self) -> None:
        sink = MemorySink()
        install_event_bus(EventBus([sink]))

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

    def test_buffered_sink_batches_and_flushes_events(self) -> None:
        class BatchSink(MemorySink):
            def emit_batch(self, events) -> None:
                self.events.extend(events)

        target = BatchSink()
        sink = BufferedEventSink(
            target,
            batch_size=10,
            flush_interval_seconds=0.05,
            queue_max_size=10,
        )
        sink.emit(TelemetryEvent("one", "test"))
        sink.emit(TelemetryEvent("two", "test"))
        sink.flush()
        sink.close()

        self.assertEqual(["one", "two"], [event.event_type for event in target.events])


if __name__ == "__main__":
    unittest.main(verbosity=2)
