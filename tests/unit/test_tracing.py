import json
import threading
import time
import unittest
from contextvars import copy_context
from datetime import date, timedelta
from unittest.mock import patch

from tests.support.tempdirs import writable_temp_directory

from src.core.telemetry.models import TelemetryEvent
from src.core.tracing import (
    TelemetryTraceSink,
    TraceDirection,
    TraceLayer,
    TraceRecorder,
    TraceWriter,
    bind_trace_context,
    install_trace_recorder,
    new_trace_context,
    record_trace,
    reset_trace_context,
)
from src.core.tracing.sanitization import MAX_DEPTH_MARKER, sanitize_trace_data
from src.core.tracing.llm import LlmTraceCallback


class MemoryWriter:
    def __init__(self):
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def flush(self):
        pass

    def close(self, _timeout=2):
        pass


class FailingWriter(MemoryWriter):
    def emit(self, _record):
        raise OSError("trace storage unavailable")


class TracingTest(unittest.TestCase):
    def tearDown(self):
        install_trace_recorder(None)

    def test_record_identity_and_sequence_propagate_across_worker_context(self):
        writer = MemoryWriter()
        recorder = TraceRecorder(writer, daemon_id="daemon")
        install_trace_recorder(recorder)
        token = new_trace_context(request_id="request-1")
        run_token = bind_trace_context(run_id="run-1", execution_id="execution-1")
        try:
            worker_context = copy_context()
            thread = threading.Thread(
                target=lambda: worker_context.run(
                    record_trace,
                    TraceDirection.INTERNAL,
                    TraceLayer.AGENT,
                    "agent.test",
                )
            )
            thread.start()
            thread.join()
            record_trace(TraceDirection.INTERNAL, TraceLayer.AGENT, "agent.second")
        finally:
            reset_trace_context(run_token)
            reset_trace_context(token)

        self.assertEqual([1, 2], [record.sequence for record in writer.records])
        self.assertEqual({"request-1"}, {record.request_id for record in writer.records})
        self.assertEqual({"run-1"}, {record.run_id for record in writer.records})
        self.assertEqual({"execution-1"}, {record.execution_id for record in writer.records})

    def test_trace_data_redacts_content_but_keeps_token_counts(self):
        value = sanitize_trace_data(
            {
                "auth_token": "secret",
                "message": "private text",
                "input_tokens": 12,
                "output_tokens": 5,
                "safe": "x" * 1000,
            }
        )
        self.assertEqual("[REDACTED]", value["auth_token"])
        self.assertEqual("[REDACTED]", value["message"])
        self.assertEqual(12, value["input_tokens"])
        self.assertEqual(5, value["output_tokens"])
        self.assertIn("trace data truncated", value["safe"])

    def test_trace_data_stops_at_bounded_nesting_depth(self):
        value = "leaf"
        for _ in range(100):
            value = {"nested": value}

        sanitized = sanitize_trace_data(value)
        for _ in range(20):
            sanitized = sanitized["nested"]
        self.assertEqual(MAX_DEPTH_MARKER, sanitized)

    def test_duration_is_normalized_without_breaking_trace(self):
        writer = MemoryWriter()
        recorder = TraceRecorder(writer)

        self.assertEqual(
            12,
            recorder.record(
                TraceDirection.INTERNAL,
                TraceLayer.AGENT,
                "agent.float_duration",
                duration_ms=12.9,
            ).duration_ms,
        )
        self.assertEqual(
            0,
            recorder.record(
                TraceDirection.INTERNAL,
                TraceLayer.AGENT,
                "agent.negative_duration",
                duration_ms=-5,
            ).duration_ms,
        )
        self.assertIsNone(
            recorder.record(
                TraceDirection.INTERNAL,
                TraceLayer.AGENT,
                "agent.invalid_duration",
                duration_ms="invalid",
            ).duration_ms
        )

    def test_writer_failure_does_not_escape_business_call(self):
        recorder = TraceRecorder(FailingWriter())
        record = recorder.record(TraceDirection.INTERNAL, TraceLayer.AGENT, "agent.test")
        self.assertEqual("agent.test", record.kind)

    def test_slow_background_writer_does_not_delay_recording(self):
        class SlowTraceWriter(TraceWriter):
            def _write_batch(self, records):
                time.sleep(0.2)
                super()._write_batch(records)

        with writable_temp_directory("tracing") as directory:
            writer = SlowTraceWriter(
                directory,
                retention_days=14,
                batch_size=1,
                flush_interval_seconds=0.01,
                queue_max_size=10,
            )
            recorder = TraceRecorder(writer)
            started = time.monotonic()
            recorder.record(TraceDirection.INTERNAL, TraceLayer.AGENT, "agent.test")
            elapsed = time.monotonic() - started
            writer.close(timeout_seconds=1)
        self.assertLess(elapsed, 0.05)

    def test_telemetry_adapter_uses_safe_whitelist(self):
        writer = MemoryWriter()
        install_trace_recorder(TraceRecorder(writer))
        TelemetryTraceSink().emit(
            TelemetryEvent(
                "tool_finished",
                "test",
                payload={"tool": "read_file", "content_preview": "private", "content_chars": 7},
            )
        )
        data = writer.records[0].data
        self.assertEqual("read_file", data["tool"])
        self.assertEqual(7, data["content_chars"])
        self.assertNotIn("content_preview", data)

    def test_writer_rotates_by_date_and_cleans_expired_directories(self):
        with writable_temp_directory("tracing") as root:
            today = date(2026, 6, 14)
            old = root / (today - timedelta(days=14)).isoformat()
            old.mkdir(parents=True)
            (old / "daemon.jsonl").write_text("{}\n", encoding="utf-8")
            writer = TraceWriter(
                root,
                retention_days=14,
                batch_size=10,
                flush_interval_seconds=0.01,
                queue_max_size=10,
                today_provider=lambda: today,
            )
            recorder = TraceRecorder(writer, daemon_id="daemon")
            install_trace_recorder(recorder)
            record = recorder.record(TraceDirection.INTERNAL, TraceLayer.AGENT, "agent.test")
            writer.flush()
            with patch("src.core.tracing.writer.shutil.rmtree") as remove_tree:
                writer.cleanup()
                remove_tree.assert_called_once_with(old)
            writer.close()

            path = root / record.timestamp.date().isoformat() / "daemon.jsonl"
            value = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual("agent.test", value["kind"])

    def test_llm_callback_records_summary_without_message_content(self):
        class Message:
            response_metadata = {"finish_reason": "stop"}
            usage_metadata = {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}

        class Generation:
            message = Message()

        class Response:
            llm_output = {}
            generations = [[Generation()]]

        writer = MemoryWriter()
        install_trace_recorder(TraceRecorder(writer))
        callback = LlmTraceCallback(purpose="parent_agent", model="demo")
        callback.on_chat_model_start(
            {"name": "demo"},
            [[object(), object()]],
            run_id="llm-1",
            invocation_params={"tools": [{}, {}]},
        )
        callback.on_llm_end(Response(), run_id="llm-1")

        self.assertEqual(
            ["llm.request_started", "llm.response_finished"],
            [record.kind for record in writer.records],
        )
        self.assertEqual(2, writer.records[0].data["message_count"])
        self.assertEqual(10, writer.records[1].data["input_tokens"])
        self.assertEqual("stop", writer.records[1].data["stop_reason"])
        self.assertEqual(writer.records[0].trace_id, writer.records[1].trace_id)


if __name__ == "__main__":
    unittest.main()
