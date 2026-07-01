import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from src.cli.commands.chat import chat_once, interactive_chat


class FakeClient:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def request(self, method, params, on_event=None):
        self.calls.append((method, params, on_event))
        return self.result


class ApprovalClient:
    def __init__(self):
        self.calls = []

    def request(self, method, params, on_event=None):
        self.calls.append((method, params, on_event))
        if method == "agent.chat":
            on_event(
                {
                    "event": "tool_approval_required",
                    "data": {
                        "request_id": "approval-1",
                        "tool": "write_workspace_file",
                        "args": {"path": "README.md"},
                        "capabilities": ["file_write"],
                        "persistable": True,
                        "reason": "Tool requires user approval.",
                    },
                }
            )
            return {
                "status": "paused",
                "stop_reason": "tool_approval",
                "message": "Tool execution is waiting for approval.",
            }
        return {"status": "ok"}


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

    def test_goal_turn_completion_is_explicit_when_done_event_did_not_announce(self):
        client = FakeClient({"status": "ok"})
        output = io.StringIO()
        with (
            patch("src.cli.commands.chat.discover_workspace_root", return_value="D:\\project"),
            redirect_stdout(output),
        ):
            chat_once(client, "user_test", "large goal", goal_mode=True)

        self.assertIn("Goal mode execution completed.", output.getvalue())

    def test_interactive_chat_resolves_tool_approval_inline(self):
        client = ApprovalClient()
        inputs = iter(["write the file", "1", "quit"])
        output = io.StringIO()
        with (
            patch("builtins.input", side_effect=lambda _prompt: next(inputs)),
            patch("src.cli.commands.chat.discover_workspace_root", return_value="D:\\project"),
            redirect_stdout(output),
        ):
            interactive_chat(client, "user_test")

        self.assertEqual(["agent.chat", "approval.resolve"], [call[0] for call in client.calls])
        self.assertEqual("approval-1", client.calls[1][1]["request_id"])
        self.assertEqual("allow_once", client.calls[1][1]["response"])
        self.assertIn("Tool approval required: write_workspace_file", output.getvalue())
        self.assertIn("Capabilities: file_write", output.getvalue())

    def test_goal_interactive_chat_mentions_goal_mode(self):
        client = FakeClient({"status": "ok"})
        inputs = iter(["quit"])
        output = io.StringIO()
        with (
            patch("builtins.input", side_effect=lambda _prompt: next(inputs)),
            redirect_stdout(output),
        ):
            interactive_chat(client, "user_test", goal_mode=True)

        self.assertIn("Goal mode enabled", output.getvalue())


if __name__ == "__main__":
    unittest.main()
