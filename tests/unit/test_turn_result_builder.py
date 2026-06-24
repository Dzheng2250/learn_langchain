import unittest

from src.core.agent.models import StopReason
from src.core.agent.result import TurnResultBuilder


class TurnResultBuilderTest(unittest.TestCase):
    def test_done_event_marks_result_ok_and_merges_data(self):
        builder = TurnResultBuilder(run_id="run-1", default_error="failed")

        builder.observe(
            {
                "event": "done",
                "data": {"status": "ok", "workspace_id": "workspace-1"},
            }
        )

        self.assertEqual(
            {"status": "ok", "run_id": "run-1", "workspace_id": "workspace-1"},
            builder.build(),
        )

    def test_error_event_copies_standard_provider_fields(self):
        builder = TurnResultBuilder(run_id=None, default_error="fallback")

        builder.observe(
            {
                "event": "error",
                "data": {
                    "message": "provider rejected input",
                    "stop_reason": "turn_error",
                    "error_category": "content_rejected",
                    "error_action": "terminate",
                    "retryable": False,
                    "provider": "provider-a",
                    "provider_code": "inspection_failed",
                    "http_status": 400,
                    "failure_source": "agent_turn",
                    "failure_stage": "parent_model_provider",
                    "failure_scope": "current_turn",
                    "user_action": "revise_input_and_retry",
                },
            }
        )
        result = builder.build()

        self.assertEqual("error", result["status"])
        self.assertEqual("provider rejected input", result["error"])
        self.assertEqual("content_rejected", result["error_category"])
        self.assertEqual("parent_model_provider", result["failure_stage"])
        self.assertFalse(result["retryable"])
        self.assertTrue(result["run_id"])

    def test_error_event_uses_defaults_when_message_and_stop_reason_are_missing(self):
        builder = TurnResultBuilder(run_id="run-2", default_error="resume failed")

        builder.observe({"event": "error", "data": {}})

        self.assertEqual("resume failed", builder.build()["error"])
        self.assertEqual(StopReason.TURN_ERROR.value, builder.build()["stop_reason"])

    def test_non_terminal_events_do_not_change_result(self):
        builder = TurnResultBuilder(run_id="run-3", default_error="failed")

        builder.observe({"event": "token", "data": {"content": "hello"}})

        self.assertEqual({"status": "error", "run_id": "run-3"}, builder.build())


if __name__ == "__main__":
    unittest.main()
