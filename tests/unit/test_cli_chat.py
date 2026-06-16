import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from src.cli.commands.chat import chat_once


class FakeClient:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def request(self, method, params, on_event=None):
        self.calls.append((method, params, on_event))
        return self.result


class CliChatTest(unittest.TestCase):
    def test_paused_turn_is_rendered_as_recoverable_state(self):
        client = FakeClient(
            {
                "status": "paused",
                "message": "Execution paused because budget_limit.",
            }
        )
        output = io.StringIO()
        with (
            patch("src.cli.commands.chat.discover_workspace_root", return_value="D:\\project"),
            redirect_stdout(output),
        ):
            chat_once(client, "user_test", "large goal", goal_mode=True)

        rendered = output.getvalue()
        self.assertIn("Execution paused because budget_limit.", rendered)
        self.assertIn("learn-agent session resume --session user_test", rendered)
        self.assertEqual("agent.chat", client.calls[0][0])
        self.assertTrue(client.calls[0][1]["goal_mode"])


if __name__ == "__main__":
    unittest.main()
