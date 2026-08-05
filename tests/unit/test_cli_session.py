import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from src.cli.commands.session import run


class FakeClient:
    def __init__(self, result=None):
        self.result = result or {"status": "ok", "goal_mode": True}
        self.calls = []

    def request(self, method, params, on_event=None):
        self.calls.append((method, params))
        if on_event:
            on_event({"event": "done", "data": {"status": "ok", "goal_mode": True}})
        return self.result


class CliSessionTest(unittest.TestCase):
    def test_resume_uses_agent_renderer_for_goal_completion(self):
        args = SimpleNamespace(
            session_action="resume",
            session="user_test",
            workspace=None,
            instruction="",
            retry_conditions=False,
        )
        output = io.StringIO()
        with (
            patch("src.cli.commands.session.discover_workspace_root", return_value="D:\\project"),
            patch("src.cli.commands.session.CoreClient", return_value=FakeClient()),
            redirect_stdout(output),
        ):
            self.assertEqual(0, run(args, SimpleNamespace()))

        self.assertIn("[goal_completed]", output.getvalue())

    def test_resume_forwards_explicit_condition_retry(self):
        client = FakeClient()
        args = SimpleNamespace(
            session_action="resume",
            session="user_test",
            workspace=None,
            instruction="retry",
            retry_conditions=True,
        )
        with (
            patch("src.cli.commands.session.discover_workspace_root", return_value="D:\\project"),
            patch("src.cli.commands.session.CoreClient", return_value=client),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(0, run(args, SimpleNamespace()))

        self.assertEqual("session.resume", client.calls[0][0])
        self.assertTrue(client.calls[0][1]["retry_conditions"])

    def test_action_required_pause_does_not_recommend_plain_resume(self):
        client = FakeClient({
            "status": "paused",
            "stop_reason": "tool_recovery_required",
            "resume_policy": "action_required",
            "message": "Recovery required.",
        })
        args = SimpleNamespace(
            session_action="resume",
            session="user_test",
            workspace=None,
            instruction="",
            retry_conditions=False,
        )
        output = io.StringIO()
        with (
            patch("src.cli.commands.session.discover_workspace_root", return_value="D:\\project"),
            patch("src.cli.commands.session.CoreClient", return_value=client),
            redirect_stdout(output),
        ):
            self.assertEqual(0, run(args, SimpleNamespace()))

        self.assertIn("tool_recovery.list/resolve", output.getvalue())
        self.assertNotIn("to continue", output.getvalue())


if __name__ == "__main__":
    unittest.main()
