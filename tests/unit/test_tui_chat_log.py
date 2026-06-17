import unittest
import asyncio
from types import MethodType

from src.tui.screens.chat import ChatScreen
from src.tui.renderer import render_event
from src.tui.widgets.chat_log import ChatLog


class TuiChatLogTest(unittest.TestCase):
    def _fake_log(self):
        log = ChatLog.__new__(ChatLog)
        log._entries = []
        log._token_buf = ""
        log._size_known = True
        log.lines = []
        log.refreshed = 0

        def clear(self):
            self.lines.clear()
            return self

        def write(self, content):
            self.lines.append(content)
            return self

        def refresh(self):
            self.refreshed += 1

        log.clear = MethodType(clear, log)
        log.write = MethodType(write, log)
        log.refresh = MethodType(refresh, log)
        return log

    def test_tokens_update_one_visible_streaming_entry(self):
        log = self._fake_log()

        ChatLog.write_token(log, "hel")
        ChatLog.write_token(log, "lo")

        self.assertEqual(["hello"], log.lines)
        self.assertEqual(2, log.refreshed)

        ChatLog.flush_tokens(log)
        self.assertEqual(["hello"], log.lines)
        self.assertEqual("", log._token_buf)
        self.assertEqual(["hello"], log._entries)

    def test_event_after_tokens_keeps_streamed_message_then_appends_event(self):
        log = self._fake_log()

        ChatLog.write_token(log, "answer")
        ChatLog.write_event(log, "[done]")

        self.assertEqual(["answer", "[done]"], log.lines)

    def test_agent_message_after_tokens_is_not_rendered_twice(self):
        log = self._fake_log()
        screen = ChatScreen.__new__(ChatScreen)
        screen._streamed_response_active = False

        def query_one(_self, _widget_type):
            return log

        screen.query_one = MethodType(query_one, screen)

        asyncio.run(
            ChatScreen._on_event(
                screen,
                {"event": "token", "data": {"content": "hello"}},
            )
        )
        asyncio.run(
            ChatScreen._on_event(
                screen,
                {
                    "event": "step",
                    "data": {"type": "agent_message", "content": "hello"},
                },
            )
        )

        self.assertEqual(["hello"], log.lines)
        self.assertEqual(["hello"], log._entries)

    def test_terminated_done_event_renders_recovery_marker(self):
        rendered = render_event(
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

        self.assertIsNone(rendered)


if __name__ == "__main__":
    unittest.main()
