import json
import sqlite3
import threading
import time
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph

from src.core.telemetry import (
    BaseEventSink,
    BufferedEventSink,
    EventBus,
    NoopEventSink,
    SQLiteEventSink,
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
from src.core.telemetry import factory as telemetry_factory
from src.core.tools.observed import ObservedToolNode
from tests.support.paths import REPOSITORY_ROOT


def _workspace_sqlite_path_or_skip(testcase, prefix: str) -> Path:
    """Return a writable SQLite path or skip under restricted Windows sandboxes."""
    root = REPOSITORY_ROOT / ".test_tmp"
    root.mkdir(exist_ok=True)
    path = root / f"{prefix}{uuid4().hex}.db"
    try:
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("CREATE TABLE __probe(value INTEGER)")
            conn.commit()
    except (OSError, PermissionError, sqlite3.Error) as exc:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        testcase.skipTest(f"SQLite file databases are unavailable in this sandbox: {exc}")
    path.unlink(missing_ok=True)
    return path

class MemorySink(BaseEventSink):
    """Test sink that keeps emitted telemetry in memory."""

    def __init__(self) -> None:
        self.events: list[TelemetryEvent] = []

    def emit(self, event: TelemetryEvent) -> None:
        self.events.append(event)


class FailingSink(BaseEventSink):
    """Test sink that verifies sink failures are isolated."""

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

    def test_observed_tool_node_returns_tool_error_for_tool_exception(self) -> None:
        sink = MemorySink()
        install_event_bus(EventBus([sink]))

        @tool
        def broken() -> str:
            """Always fail."""
            raise RuntimeError("boom")

        node = ObservedToolNode([broken])
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
                                "name": "broken",
                                "args": {},
                                "id": "call-1",
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            }
        )

        message = output["messages"][-1]
        self.assertEqual("error", getattr(message, "status", None))
        self.assertIn("Tool broken failed", message.content)
        self.assertEqual(["tool_started", "tool_failed"], [event.event_type for event in sink.events])

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

    def test_sqlite_sink_persists_queryable_events_and_prunes_expired_rows(self) -> None:
        path = _workspace_sqlite_path_or_skip(self, "learn-agent-telemetry-")
        self.addCleanup(path.unlink, missing_ok=True)
        now = datetime.now(timezone.utc)
        sink = SQLiteEventSink(path, retention_days=30)
        sink.emit_batch(
            [
                TelemetryEvent(
                    "expired",
                    "test",
                    created_at=now - timedelta(days=31),
                ),
                TelemetryEvent(
                    "turn_finished",
                    "agent_service",
                    payload={"stop_reason": "completed"},
                    workspace_id=uuid4(),
                    session_id=uuid4(),
                    turn_index=3,
                    run_id="run-1",
                    created_at=now,
                ),
            ]
        )

        # Retention is enforced during process-level sink initialization.
        SQLiteEventSink(path, retention_days=30)
        with closing(sqlite3.connect(path)) as conn:
            rows = conn.execute(
                """
                SELECT event_type, run_id, turn_index, payload
                FROM telemetry_events ORDER BY event_id
                """
            ).fetchall()

        self.assertEqual(1, len(rows))
        self.assertEqual(("turn_finished", "run-1", 3), rows[0][:3])
        self.assertEqual("completed", json.loads(rows[0][3])["stop_reason"])

    def test_sqlite_sink_initialization_failure_falls_back_without_stopping_core(self) -> None:
        with (
            patch.object(telemetry_factory, "AGENT_EVENTS_SQLITE_ENABLED", True),
            patch.object(telemetry_factory, "AGENT_EVENTS_FILE_ENABLED", False),
            patch.object(telemetry_factory, "AGENT_EVENTS_POSTGRES_ENABLED", False),
            patch.object(
                telemetry_factory,
                "SQLiteEventSink",
                side_effect=OSError("read-only path"),
            ),
        ):
            bus = telemetry_factory.create_event_bus()

        self.addCleanup(bus.close)
        self.assertEqual(1, len(bus.sinks))
        self.assertIsInstance(bus.sinks[0], NoopEventSink)

    def test_event_bus_factory_wires_sqlite_as_default_structured_sink(self) -> None:
        path = _workspace_sqlite_path_or_skip(self, "learn-agent-telemetry-factory-")
        self.addCleanup(path.unlink, missing_ok=True)
        with (
            patch.object(telemetry_factory, "AGENT_EVENTS_SQLITE_ENABLED", True),
            patch.object(telemetry_factory, "AGENT_EVENTS_SQLITE_PATH", str(path)),
            patch.object(telemetry_factory, "AGENT_EVENTS_ASYNC_WRITE", False),
            patch.object(telemetry_factory, "AGENT_EVENTS_FILE_ENABLED", False),
            patch.object(telemetry_factory, "AGENT_EVENTS_POSTGRES_ENABLED", False),
        ):
            bus = telemetry_factory.create_event_bus()
        bus.publish(TelemetryEvent("turn_started", "test", run_id="run-factory"))
        bus.close()

        with closing(sqlite3.connect(path)) as conn:
            row = conn.execute(
                "SELECT event_type, run_id FROM telemetry_events"
            ).fetchone()

        self.assertEqual(("turn_started", "run-factory"), row)

    def test_buffered_sqlite_sink_handles_burst_without_blocking_producers(self) -> None:
        path = _workspace_sqlite_path_or_skip(self, "learn-agent-telemetry-burst-")
        self.addCleanup(path.unlink, missing_ok=True)
        sink = BufferedEventSink(
            SQLiteEventSink(path),
            batch_size=100,
            flush_interval_seconds=0.01,
            queue_max_size=2000,
        )

        started = time.perf_counter()
        for index in range(1000):
            sink.emit(
                TelemetryEvent(
                    "tool_finished",
                    "burst-test",
                    run_id=f"run-{index // 100}",
                )
            )
        producer_seconds = time.perf_counter() - started
        sink.flush()
        sink.close()

        with closing(sqlite3.connect(path)) as conn:
            count = conn.execute(
                "SELECT count(*) FROM telemetry_events"
            ).fetchone()[0]

        self.assertEqual(1000, count)
        self.assertLess(producer_seconds, 0.5)

    def test_slow_batch_sink_does_not_block_event_producer(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        class SlowBatchSink(MemorySink):
            def emit_batch(self, events) -> None:
                entered.set()
                release.wait(timeout=1)
                self.events.extend(events)

        target = SlowBatchSink()
        sink = BufferedEventSink(
            target,
            batch_size=1,
            flush_interval_seconds=0.01,
            queue_max_size=10,
        )
        started = time.perf_counter()
        sink.emit(TelemetryEvent("turn_started", "test"))
        producer_seconds = time.perf_counter() - started

        self.assertTrue(entered.wait(timeout=1))
        self.assertLess(producer_seconds, 0.05)
        release.set()
        sink.close()
        self.assertEqual(1, len(target.events))


if __name__ == "__main__":
    unittest.main(verbosity=2)
