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


if __name__ == "__main__":
    unittest.main()
