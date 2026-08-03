import io
import unittest
from contextlib import redirect_stdout

from src.cli.render import AgentEventRenderer


class AgentEventRendererTest(unittest.TestCase):
    def test_goal_continuation_is_visible(self):
        output = io.StringIO()
        with redirect_stdout(output):
            AgentEventRenderer().render({
                "event": "goal_continuation_started",
                "data": {"slice_number": 1},
            })

        self.assertIn("checking unfinished tasks", output.getvalue())
    def test_resource_activity_summary_uses_shared_core_shape(self):
        output = io.StringIO()
        with redirect_stdout(output):
            AgentEventRenderer().render({"event": "resource_activity_summary", "data": {"summary": {
                "reads": {"resource_count": 2, "returned_bytes": 120},
                "changes": {"changed_resource_count": 1},
                "evidence": {"missing": 1},
            }}})
        self.assertIn("read 2 resource(s), 120 bytes", output.getvalue())
        self.assertIn("changed 1; warnings 1", output.getvalue())
    def test_completed_agent_message_is_rendered_when_provider_emits_no_tokens(self):
        output = io.StringIO()
        renderer = AgentEventRenderer()
        with redirect_stdout(output):
            renderer.render(
                {
                    "event": "step",
                    "data": {"type": "agent_message", "content": "final answer"},
                }
            )
        self.assertEqual("final answer", output.getvalue())

    def test_completed_agent_message_is_not_repeated_after_streamed_tokens(self):
        output = io.StringIO()
        renderer = AgentEventRenderer()
        with redirect_stdout(output):
            renderer.render({"event": "token", "data": {"content": "final "}})
            renderer.render({"event": "token", "data": {"content": "answer"}})
            renderer.render(
                {
                    "event": "step",
                    "data": {"type": "agent_message", "content": "final answer"},
                }
            )
        self.assertEqual("final answer", output.getvalue())

    def test_retry_events_mark_stale_attempt_and_next_retry(self):
        output = io.StringIO()
        renderer = AgentEventRenderer()
        with redirect_stdout(output):
            renderer.render(
                {
                    "event": "token",
                    "data": {"content": "draft", "attempt_id": "attempt-1"},
                }
            )
            renderer.render(
                {
                    "event": "model_attempt_invalidated",
                    "data": {"attempt": 1, "error_category": "service_unavailable"},
                }
            )
            renderer.render(
                {
                    "event": "model_retry_scheduled",
                    "data": {
                        "next_attempt": 2,
                        "max_attempts": 3,
                        "delay_seconds": 1.25,
                    },
                }
            )

        rendered = output.getvalue()
        self.assertIn("draft", rendered)
        self.assertIn("[model_attempt_stale: attempt 1, service_unavailable]", rendered)
        self.assertIn("[model_retry: attempt 2/3 in 1.25s]", rendered)
        self.assertFalse(renderer.received_token)
        self.assertIsNone(renderer.current_attempt_id)

    def test_retry_exhausted_is_rendered_as_progress_marker(self):
        output = io.StringIO()
        renderer = AgentEventRenderer()
        with redirect_stdout(output):
            renderer.render(
                {
                    "event": "model_retry_exhausted",
                    "data": {"error_category": "rate_limited"},
                }
            )

        self.assertIn("[model_retry_exhausted: rate_limited]", output.getvalue())

    def test_task_plan_start_renders_plan_details(self):
        output = io.StringIO()
        renderer = AgentEventRenderer()
        with redirect_stdout(output):
            renderer.render(
                {
                    "event": "step",
                    "data": {
                        "type": "tool_call_start",
                        "tool": "task_plan",
                        "args": {
                            "tasks": [
                                {
                                    "task_key": "inspect_structure",
                                    "subject": "Inspect project structure",
                                },
                                {
                                    "task_key": "review_design",
                                    "subject": "Review odd design choices",
                                    "depends_on": ["inspect_structure"],
                                },
                            ]
                        },
                    },
                }
            )

        rendered = output.getvalue()
        self.assertIn("[tool_call_start: task_plan]", rendered)
        self.assertIn("Task plan:", rendered)
        self.assertIn("inspect_structure: Inspect project structure", rendered)
        self.assertIn("review_design: Review odd design choices", rendered)

    def test_task_update_start_renders_status_change(self):
        output = io.StringIO()
        renderer = AgentEventRenderer()
        with redirect_stdout(output):
            renderer.render(
                {
                    "event": "step",
                    "data": {
                        "type": "tool_call_start",
                        "tool": "task_update",
                        "args": {
                            "task_key": "inspect_structure",
                            "status": "in_progress",
                        },
                    },
                }
            )

        rendered = output.getvalue()
        self.assertIn("[tool_call_start: task_update]", rendered)
        self.assertIn("Task update: inspect_structure", rendered)
        self.assertIn("status=in_progress", rendered)

    def test_task_result_content_is_visible_but_limited(self):
        output = io.StringIO()
        renderer = AgentEventRenderer()
        with redirect_stdout(output):
            renderer.render(
                {
                    "event": "step",
                    "data": {
                        "type": "tool_call_result",
                        "tool": "task_list",
                        "content": "[ ] inspect_structure\n" + "x" * 2000,
                    },
                }
            )

        rendered = output.getvalue()
        self.assertIn("[tool_call_result: task_list]", rendered)
        self.assertIn("[ ] inspect_structure", rendered)
        self.assertIn("... truncated ...", rendered)

    def test_generic_tool_start_renders_safe_args(self):
        output = io.StringIO()
        renderer = AgentEventRenderer()
        with redirect_stdout(output):
            renderer.render(
                {
                    "event": "step",
                    "data": {
                        "type": "tool_call_start",
                        "tool": "run_command_in_container",
                        "args": {"command": "python -m unittest", "timeout": 30},
                    },
                }
            )

        rendered = output.getvalue()
        self.assertIn("[tool_call_start: run_command_in_container]", rendered)
        self.assertIn("Args:", rendered)
        self.assertIn("python -m unittest", rendered)
        self.assertIn("timeout", rendered)

    def test_generic_tool_start_redacts_sensitive_and_truncates_long_args(self):
        output = io.StringIO()
        renderer = AgentEventRenderer()
        with redirect_stdout(output):
            renderer.render(
                {
                    "event": "step",
                    "data": {
                        "type": "tool_call_start",
                        "tool": "write_workspace_file",
                        "args": {
                            "path": "notes.txt",
                            "api_key": "should-not-render",
                            "content": "x" * 500,
                        },
                    },
                }
            )

        rendered = output.getvalue()
        self.assertIn("Write: notes.txt", rendered)
        self.assertIn("500 bytes", rendered)
        self.assertNotIn("should-not-render", rendered)
        self.assertNotIn("x" * 100, rendered)


    def test_write_approval_hides_content_body(self):
        output = io.StringIO()
        renderer = AgentEventRenderer()
        with redirect_stdout(output):
            renderer.render({
                "event": "tool_approval_required",
                "data": {
                    "request_id": "request",
                    "tool": "write_workspace_file",
                    "args": {"path": "notes.txt", "content": "private-body"},
                },
            })
        rendered = output.getvalue()
        self.assertIn("Write: notes.txt", rendered)
        self.assertNotIn("private-body", rendered)
    def test_goal_done_event_renders_completion_marker(self):
        output = io.StringIO()
        renderer = AgentEventRenderer(goal_mode=True)
        with redirect_stdout(output):
            renderer.render({"event": "done", "data": {"status": "ok"}})

        self.assertIn("[goal_completed]", output.getvalue())
        self.assertTrue(renderer.done_announced)

    def test_paused_done_event_renders_pause_marker(self):
        output = io.StringIO()
        renderer = AgentEventRenderer()
        with redirect_stdout(output):
            renderer.render(
                {
                    "event": "done",
                    "data": {"status": "paused", "stop_reason": "budget_limit"},
                }
            )

        self.assertIn("[execution_paused: budget_limit]", output.getvalue())
        self.assertTrue(renderer.done_announced)

    def test_terminated_done_event_renders_recovery_marker(self):
        output = io.StringIO()
        renderer = AgentEventRenderer()
        with redirect_stdout(output):
            renderer.render(
                {
                    "event": "done",
                    "data": {
                        "status": "terminated",
                        "auto_recovered": True,
                        "failure_source": "agent_turn",
                        "failure_scope": "current_turn",
                        "failure_stage": "parent_model_provider",
                    },
                }
            )

        self.assertEqual("", output.getvalue())
        self.assertTrue(renderer.done_announced)

    def test_error_event_is_recorded_but_not_rendered_immediately(self):
        output = io.StringIO()
        renderer = AgentEventRenderer()
        with redirect_stdout(output):
            renderer.render({"event": "error", "data": {"message": "failed once"}})

        self.assertEqual("", output.getvalue())
        self.assertEqual("failed once", renderer.error_message)


if __name__ == "__main__":
    unittest.main()
