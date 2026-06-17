import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from src.cli.commands.session import run


class FakeClient:
    def request(self, method, params, on_event=None):
        if on_event:
            on_event({"event": "done", "data": {"status": "ok", "goal_mode": True}})
        return {"status": "ok", "goal_mode": True}


class CliSessionTest(unittest.TestCase):
    def test_resume_uses_agent_renderer_for_goal_completion(self):
        args = SimpleNamespace(
            session_action="resume",
            session="user_test",
            workspace=None,
            instruction="",
        )
        output = io.StringIO()
        with (
            patch("src.cli.commands.session.discover_workspace_root", return_value="D:\\project"),
            patch("src.cli.commands.session.CoreClient", return_value=FakeClient()),
            redirect_stdout(output),
        ):
            self.assertEqual(0, run(args, SimpleNamespace()))

        self.assertIn("[goal_completed]", output.getvalue())


if __name__ == "__main__":
    unittest.main()
