import unittest
from types import MethodType

from src.tui.widgets.chat_log import ChatLog


class TuiChatLogTest(unittest.TestCase):
    def _fake_log(self):
        log = ChatLog.__new__(ChatLog)
        log._token_buf = ""
        log._token_line_start = None
        log._size_known = True
        log.lines = []
        log.refreshed = 0

        def write(self, content):
            self.lines.append(content)
            return self

        def refresh(self):
            self.refreshed += 1

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
        self.assertIsNone(log._token_line_start)

    def test_event_after_tokens_keeps_streamed_message_then_appends_event(self):
        log = self._fake_log()

        ChatLog.write_token(log, "answer")
        ChatLog.write_event(log, "[done]")

        self.assertEqual(["answer", "[done]"], log.lines)


if __name__ == "__main__":
    unittest.main()
