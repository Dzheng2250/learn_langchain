import io
import unittest
from unittest.mock import patch

from src.cli.client import CoreClient
from src.cli.commands.chat import interactive_chat
from src.cli.config import CliConfig
from src.cli.daemon import daemon_status
from src.cli.errors import (
    CliError,
    CoreAuthenticationError,
    CoreConnectionInterruptedError,
    CoreProtocolError,
    CoreUnavailableError,
)
from src.cli.main import main


class FakeStream:
    def __init__(self, response: bytes):
        self.response = io.BytesIO(response)

    def write(self, _data):
        pass

    def flush(self):
        pass

    def readline(self):
        return self.response.readline()


class FakeSocket:
    def __init__(self, response: bytes):
        self.stream = FakeStream(response)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def settimeout(self, _timeout):
        pass

    def makefile(self, _mode):
        return self.stream


class CliResilienceTest(unittest.TestCase):
    def setUp(self):
        self.config = CliConfig.load()
        self.client = CoreClient(self.config)

    def test_missing_token_becomes_core_unavailable(self):
        with patch("src.cli.client.read_token", side_effect=FileNotFoundError):
            with self.assertRaises(CoreUnavailableError) as raised:
                self.client.request("core.ping")
        self.assertIn("learn-agent start", raised.exception.hint)

    def test_connection_refused_becomes_core_unavailable(self):
        with (
            patch("src.cli.client.read_token", return_value="token"),
            patch("src.cli.client.socket.create_connection", side_effect=ConnectionRefusedError),
        ):
            with self.assertRaises(CoreUnavailableError):
                self.client.request("core.ping")

    def test_connection_close_becomes_interrupted_error(self):
        with (
            patch("src.cli.client.read_token", return_value="token"),
            patch("src.cli.client.socket.create_connection", return_value=FakeSocket(b"")),
        ):
            with self.assertRaises(CoreConnectionInterruptedError):
                self.client.request("agent.chat")

    def test_invalid_response_becomes_protocol_error(self):
        with (
            patch("src.cli.client.read_token", return_value="token"),
            patch(
                "src.cli.client.socket.create_connection",
                return_value=FakeSocket(b"not-json\n"),
            ),
        ):
            with self.assertRaises(CoreProtocolError):
                self.client.request("core.ping")

    @patch("src.cli.commands.status.run", side_effect=CliError("friendly", hint="next step"))
    def test_main_renders_expected_error_without_traceback(self, _run):
        with patch("builtins.print") as output:
            code = main(["status"])
        self.assertEqual(1, code)
        rendered = "\n".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn("Error: friendly", rendered)
        self.assertIn("Hint: next step", rendered)

    def test_status_does_not_hide_authentication_error(self):
        with patch(
            "src.cli.daemon.CoreClient.request",
            side_effect=CoreAuthenticationError("bad token"),
        ):
            with self.assertRaises(CoreAuthenticationError):
                daemon_status(self.config)

    def test_interactive_chat_continues_after_one_failed_turn(self):
        inputs = iter(["first", "second", "quit"])
        with (
            patch("builtins.input", side_effect=lambda _prompt: next(inputs)),
            patch(
                "src.cli.commands.chat.chat_once",
                side_effect=[CoreUnavailableError("down"), None],
            ) as chat_once,
            patch("src.cli.commands.chat.render_cli_error"),
        ):
            interactive_chat(self.client, "session")
        self.assertEqual(2, chat_once.call_count)


if __name__ == "__main__":
    unittest.main()
