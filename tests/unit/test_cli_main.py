import unittest
from unittest.mock import patch

from src.cli.main import main


class CliMainTest(unittest.TestCase):
    @patch("src.cli.commands.start.start_daemon", return_value={"uptime_ms": 12})
    def test_start_command_dispatches_to_handler(self, start_daemon):
        self.assertEqual(0, main(["start"]))
        start_daemon.assert_called_once()

    @patch("src.cli.commands.status.daemon_status", return_value=None)
    def test_status_returns_nonzero_when_daemon_is_down(self, daemon_status):
        self.assertEqual(1, main(["status"]))
        daemon_status.assert_called_once()

    @patch("src.cli.commands.chat.chat_once")
    @patch("src.cli.commands.chat.CoreClient")
    def test_chat_command_uses_default_session(self, core_client, chat_once):
        self.assertEqual(0, main(["chat", "hello"]))
        chat_once.assert_called_once()
        self.assertEqual("default", chat_once.call_args.args[1])
        self.assertEqual("hello", chat_once.call_args.args[2])
        self.assertIsNone(chat_once.call_args.args[3])
        self.assertFalse(chat_once.call_args.kwargs["goal_mode"])

    @patch("src.cli.commands.chat.chat_once")
    @patch("src.cli.commands.chat.CoreClient")
    def test_chat_goal_mode_is_explicit(self, core_client, chat_once):
        self.assertEqual(0, main(["chat", "--goal", "build the feature"]))
        self.assertTrue(chat_once.call_args.kwargs["goal_mode"])

    @patch("src.cli.commands.stop.stop_daemon", return_value={"status": "forced_stopped"})
    def test_stop_command_accepts_force(self, stop_daemon):
        self.assertEqual(0, main(["stop", "--force"]))
        self.assertTrue(stop_daemon.call_args.kwargs["force"])


if __name__ == "__main__":
    unittest.main()
