import io
import unittest
from contextlib import redirect_stdout

from src.cli.render import AgentEventRenderer


class AgentEventRendererTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
