import asyncio
import unittest
from types import MethodType
from unittest.mock import patch

from rich.markdown import Markdown
from rich.text import Text

from src.tui.screens.chat import ChatScreen
from src.tui.renderer import render_event
from src.tui.widgets.chat_log import ChatLog, _LogEntry


class FakeEntryWidget:
    def __init__(self, renderable):
        self.renderable = renderable
        self.updates = 0

    def update(self, renderable):
        self.renderable = renderable
        self.updates += 1


def _rendered_text(value):
    if isinstance(value, FakeEntryWidget):
        return _rendered_text(value.renderable)
    if isinstance(value, Text):
        return value.plain
    if isinstance(value, Markdown):
        return value.markup
    return value


class TuiChatLogTest(unittest.TestCase):
    def _fake_log(self):
        log = ChatLog.__new__(ChatLog)
        log._entries = []
        log._token_buf = ""
        log._active_token_widget = None
        log._stream_committed_length = 0
        log._markdown_render_limit = 50_000
        log._partial_markdown_min_chars = 240
        log._partial_markdown_max_tail_chars = 1_200
        log._auto_scroll = True
        log._user_scroll_paused = False
        log._user_scroll_pause_until = 0.0
        log._force_follow_until = 0.0
        log._is_scroll_end = True
        log._is_scrollbar_grabbed = False
        log._scroll_y = 0
        log._max_scroll_y = 0
        log.widgets = []
        log.scrolled = 0
        log.scrolled_up = 0
        log.scrolled_down = 0

        def mount(self, widget):
            self.widgets.append(widget)
            return widget

        def remove_children(self):
            self.widgets.clear()

        def scroll_end(self, **_kwargs):
            self.scrolled += 1
            self._is_scroll_end = True

        def scroll_up(self, **_kwargs):
            self.scrolled_up += 1
            self._scroll_y = max(0, self._scroll_y - 1)
            self._is_scroll_end = False

        def scroll_down(self, **_kwargs):
            self.scrolled_down += 1
            self._scroll_y = min(self._max_scroll_y, self._scroll_y + 1)
            self._is_scroll_end = self._scroll_y >= self._max_scroll_y

        type(log).is_vertical_scroll_end = property(lambda self: self._is_scroll_end)
        type(log).is_vertical_scrollbar_grabbed = property(
            lambda self: self._is_scrollbar_grabbed
        )
        type(log).scroll_y = property(lambda self: self._scroll_y)
        type(log).max_scroll_y = property(lambda self: self._max_scroll_y)

        def _new_widget(self, entry):
            if entry.mode == "markup":
                return FakeEntryWidget(entry.content)
            return FakeEntryWidget(ChatLog._renderable_for_entry(self, entry))

        log.mount = MethodType(mount, log)
        log.remove_children = MethodType(remove_children, log)
        def call_after_refresh(self, callback):
            callback()

        log.scroll_end = MethodType(scroll_end, log)
        log.scroll_up = MethodType(scroll_up, log)
        log.scroll_down = MethodType(scroll_down, log)
        log.call_after_refresh = MethodType(call_after_refresh, log)
        log._new_widget = MethodType(_new_widget, log)
        return log

    def assert_widget_texts(self, log, expected):
        self.assertEqual(expected, [_rendered_text(widget) for widget in log.widgets])

    def render_frame(self, log):
        ChatLog.render_pending_tokens(log)

    def test_tokens_update_one_visible_streaming_entry(self):
        log = self._fake_log()

        ChatLog.write_token(log, "hel")
        ChatLog.write_token(log, "lo")
        self.render_frame(log)

        self.assertEqual(1, len(log.widgets))
        self.assert_widget_texts(log, ["hello"])
        self.assertIsInstance(log.widgets[-1].renderable, Text)
        self.assertEqual(0, log.widgets[-1].updates)

        ChatLog.flush_tokens(log)
        self.assertEqual(1, len(log.widgets))
        self.assert_widget_texts(log, ["hello"])
        self.assertIsInstance(log.widgets[-1].renderable, Markdown)
        self.assertEqual("", log._token_buf)
        self.assertIsNone(log._active_token_widget)
        self.assertEqual(0, log._stream_committed_length)
        self.assertEqual([_LogEntry("hello", "markdown")], log._entries)

    def test_long_token_stream_stages_markdown_without_replaying_history(self):
        log = self._fake_log()

        for _ in range(1000):
            ChatLog.write_token(log, "word ")
        self.render_frame(log)

        rendered = "".join(_rendered_text(widget) for widget in log.widgets)
        self.assertEqual("word " * 1000, rendered)
        self.assertLess(len(log.widgets), 10)
        self.assertTrue(any(isinstance(widget.renderable, Markdown) for widget in log.widgets))

        ChatLog.flush_tokens(log)

        rendered = "".join(_rendered_text(widget) for widget in log.widgets)
        self.assertEqual("word " * 1000, rendered)
        self.assertTrue(all(isinstance(widget.renderable, Markdown) for widget in log.widgets))

    def test_completed_paragraph_is_markdown_while_tail_keeps_streaming_plain(self):
        log = self._fake_log()
        log._partial_markdown_min_chars = 5

        ChatLog.write_token(log, "**done**\n\nstill streaming")
        self.render_frame(log)

        self.assertEqual(2, len(log.widgets))
        self.assertIsInstance(log.widgets[0].renderable, Markdown)
        self.assertEqual("**done**\n\n", _rendered_text(log.widgets[0]))
        self.assertIsInstance(log.widgets[1].renderable, Text)
        self.assertEqual("still streaming", _rendered_text(log.widgets[1]))

    def test_streaming_text_is_plain_until_final_markdown(self):
        log = self._fake_log()

        ChatLog.write_token(log, "[bold]not markup[/bold]")
        self.render_frame(log)

        self.assertIsInstance(log.widgets[-1].renderable, Text)
        self.assertEqual("[bold]not markup[/bold]", _rendered_text(log.widgets[-1]))

        ChatLog.flush_tokens(log)
        self.assertIsInstance(log.widgets[-1].renderable, Markdown)

    def test_long_completed_stream_falls_back_to_plain_text(self):
        log = self._fake_log()
        log._markdown_render_limit = 5

        ChatLog.write_token(log, "long answer")
        ChatLog.flush_tokens(log)

        self.assertIsInstance(log.widgets[-1].renderable, Text)
        self.assertEqual("long answer", _rendered_text(log.widgets[-1]))

    def test_event_after_tokens_keeps_streamed_message_then_appends_event(self):
        log = self._fake_log()

        ChatLog.write_token(log, "answer")
        self.render_frame(log)
        ChatLog.write_event(log, "[done]")

        self.assert_widget_texts(log, ["answer", "[done]"])
        self.assertIsInstance(log.widgets[0].renderable, Markdown)

    def test_mark_tokens_stale_keeps_draft_as_incomplete_entry(self):
        log = self._fake_log()

        ChatLog.write_token(log, "partial draft")
        self.render_frame(log)
        ChatLog.mark_tokens_stale(log, "retrying after provider error")

        rendered = [_rendered_text(widget) for widget in log.widgets]
        self.assertEqual("", log._token_buf)
        self.assertIsNone(log._active_token_widget)
        self.assertEqual(0, log._stream_committed_length)
        self.assertTrue(any("INCOMPLETE MODEL DRAFT" in line for line in rendered))
        self.assertTrue(any("partial draft" in line for line in rendered))
        self.assertTrue(any("retrying after provider error" in line for line in rendered))

    def test_clear_resets_log_widgets_and_stream_state(self):
        log = self._fake_log()

        ChatLog.write_token(log, "partial")
        self.render_frame(log)
        ChatLog.clear(log)

        self.assertEqual([], log.widgets)
        self.assertEqual([], log._entries)
        self.assertEqual("", log._token_buf)
        self.assertIsNone(log._active_token_widget)
        self.assertEqual(0, log._stream_committed_length)

    def test_tui_retry_invalidation_marks_current_stream_stale(self):
        log = self._fake_log()
        screen = ChatScreen.__new__(ChatScreen)
        screen._streamed_response_active = True

        def query_one(_self, _widget_type):
            return log

        screen.query_one = MethodType(query_one, screen)

        asyncio.run(
            ChatScreen._on_event(
                screen,
                {"event": "token", "data": {"content": "draft"}},
            )
        )
        asyncio.run(
            ChatScreen._on_event(
                screen,
                {
                    "event": "model_attempt_invalidated",
                    "data": {"attempt": 1, "error_category": "timeout"},
                },
            )
        )

        rendered = [_rendered_text(widget) for widget in log.widgets]
        self.assertFalse(screen._streamed_response_active)
        self.assertTrue(any("INCOMPLETE MODEL DRAFT" in line for line in rendered))
        self.assertTrue(any("timeout" in line for line in rendered))

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

        self.assert_widget_texts(log, ["hello"])
        self.assertEqual([_LogEntry("hello", "markdown")], log._entries)

    def test_token_events_yield_to_textual_event_loop(self):
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

        self.render_frame(log)
        self.assertEqual("hello", _rendered_text(log.widgets[-1]))

    def test_action_submit_starts_background_input_task(self):
        class FakeInputBar:
            def __init__(self):
                self.text = "hello"

        bar = FakeInputBar()
        screen = ChatScreen.__new__(ChatScreen)
        screen._busy = False
        started = asyncio.Event()
        release = asyncio.Event()
        handled = []

        def query_one(_self, _widget_type):
            return bar

        async def handle_input(_self, text):
            handled.append(text)
            started.set()
            await release.wait()

        screen.query_one = MethodType(query_one, screen)
        screen._handle_input = MethodType(handle_input, screen)

        async def run():
            await ChatScreen.action_submit(screen)
            self.assertEqual("", bar.text)
            self.assertFalse(screen._input_task.done())
            await asyncio.wait_for(started.wait(), timeout=1)
            self.assertEqual(["hello"], handled)
            release.set()
            await screen._input_task

        asyncio.run(run())

    def test_real_screen_queues_events_instead_of_rendering_inline(self):
        log = self._fake_log()
        screen = ChatScreen.__new__(ChatScreen)
        screen._streamed_response_active = False
        screen._event_queue = asyncio.Queue(maxsize=10)
        screen._event_worker_task = None

        def query_one(_self, _widget_type):
            return log

        async def drain_once(_self):
            params = await _self._event_queue.get()
            try:
                await ChatScreen._render_event(_self, params)
            finally:
                _self._event_queue.task_done()

        screen.query_one = MethodType(query_one, screen)
        screen._ensure_event_worker = MethodType(lambda _self: None, screen)
        screen._drain_event_queue = MethodType(drain_once, screen)

        async def run():
            await ChatScreen._on_event(
                screen, {"event": "token", "data": {"content": "queued"}}
            )
            self.assertEqual(1, screen._event_queue.qsize())
            self.assertEqual([], log.widgets)
            await drain_once(screen)

        asyncio.run(run())

        self.render_frame(log)
        self.assertEqual("queued", _rendered_text(log.widgets[-1]))

    def test_event_consumer_survives_bad_event_render(self):
        screen = ChatScreen.__new__(ChatScreen)
        screen._event_queue = asyncio.Queue(maxsize=10)
        failures = []

        async def bad_render(_self, _params):
            raise RuntimeError("bad event")

        def log_failure(_self, exc):
            failures.append(str(exc))

        screen._render_event = MethodType(bad_render, screen)
        screen._log_event_render_failure = MethodType(log_failure, screen)

        async def run():
            await screen._event_queue.put({"event": "broken"})
            worker = asyncio.create_task(ChatScreen._drain_event_queue(screen))
            await screen._event_queue.join()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

        asyncio.run(run())

        self.assertEqual(["bad event"], failures)


    def test_streaming_does_not_force_scroll_when_user_is_reading_history(self):
        log = self._fake_log()
        log._is_scroll_end = False
        log._scroll_y = 5
        log._max_scroll_y = 20

        ChatLog.write_token(log, "new output")
        self.render_frame(log)

        self.assertFalse(log._auto_scroll)
        self.assertEqual(0, log.scrolled)
        self.assertEqual("new output", _rendered_text(log.widgets[-1]))

    def test_numeric_scroll_position_overrides_stale_end_flag(self):
        log = self._fake_log()
        log._is_scroll_end = True
        log._scroll_y = 0
        log._max_scroll_y = 20

        ChatLog.write_token(log, "new output")
        self.render_frame(log)

        self.assertFalse(log._auto_scroll)
        self.assertEqual(0, log.scrolled)

    def test_mouse_scroll_up_pauses_follow_tail_without_blocking_scroll_state(self):
        log = self._fake_log()

        ChatLog.on_mouse_scroll_up(log, object())
        log._scroll_y = 5
        log._max_scroll_y = 20
        ChatLog.write_token(log, "new output")

        self.assertTrue(log._user_scroll_paused)
        self.assertFalse(log._auto_scroll)
        self.assertEqual(1, log.scrolled_up)
        self.assertEqual(0, log.scrolled)

    def test_paused_follow_tail_is_not_cleared_by_stale_bottom_state(self):
        log = self._fake_log()
        ChatLog.pause_auto_scroll(log)
        log._is_scroll_end = True
        log._scroll_y = 20
        log._max_scroll_y = 20

        ChatLog.write_token(log, "new output")
        self.render_frame(log)

        self.assertTrue(log._user_scroll_paused)
        self.assertFalse(log._auto_scroll)
        self.assertEqual(0, log.scrolled)

    def test_recent_user_scroll_blocks_follow_tail_during_stream_frame(self):
        log = self._fake_log()
        ChatLog.pause_auto_scroll(log)
        log._user_scroll_paused = False
        log._auto_scroll = True
        log._is_scroll_end = True
        log._scroll_y = 20
        log._max_scroll_y = 20

        ChatLog.write_token(log, "new output")
        self.render_frame(log)

        self.assertFalse(log._auto_scroll)
        self.assertEqual(0, log.scrolled)

    def test_scroll_y_watcher_pauses_follow_tail_after_real_scroll_movement(self):
        log = self._fake_log()
        log._scroll_y = 10
        log._max_scroll_y = 20

        with patch("textual.widget.Widget.watch_scroll_y", return_value=None) as base:
            ChatLog.watch_scroll_y(log, 10, 4)

        base.assert_called_once_with(10, 4)
        self.assertTrue(log._user_scroll_paused)
        self.assertFalse(log._auto_scroll)

    def test_scroll_y_watcher_resumes_when_user_reaches_bottom(self):
        log = self._fake_log()
        ChatLog.pause_auto_scroll(log)
        log._scroll_y = 20
        log._max_scroll_y = 20

        with patch("textual.widget.Widget.watch_scroll_y", return_value=None):
            ChatLog.watch_scroll_y(log, 10, 20)

        self.assertFalse(log._user_scroll_paused)
        self.assertTrue(log._auto_scroll)

    def test_force_scroll_to_bottom_resumes_follow_tail_for_new_input(self):
        log = self._fake_log()
        ChatLog.pause_auto_scroll(log)

        ChatLog.force_scroll_to_bottom(log)
        ChatLog.write_token(log, "fresh output")
        self.render_frame(log)

        self.assertFalse(log._user_scroll_paused)
        self.assertTrue(log._auto_scroll)
        self.assertGreater(log.scrolled, 0)

    def test_new_input_forces_follow_even_before_layout_updates_scroll_position(self):
        log = self._fake_log()
        log._is_scroll_end = False
        log._scroll_y = 0
        log._max_scroll_y = 40

        ChatLog.force_scroll_to_bottom(log)
        ChatLog.write_event(log, "[bold]▶ sending[/bold]")

        self.assertTrue(log._auto_scroll)
        self.assertFalse(log._user_scroll_paused)
        self.assertGreater(log.scrolled, 0)

    def test_user_scroll_after_new_input_cancels_forced_follow(self):
        log = self._fake_log()

        ChatLog.force_scroll_to_bottom(log)
        with patch("textual.widget.Widget.watch_scroll_y", return_value=None):
            ChatLog.watch_scroll_y(log, 20, 5)
        ChatLog.write_token(log, "streaming")
        self.render_frame(log)

        self.assertTrue(log._user_scroll_paused)
        self.assertFalse(log._auto_scroll)

    def test_streaming_resumes_auto_scroll_after_user_returns_to_bottom(self):
        log = self._fake_log()
        log._is_scroll_end = False
        log._scroll_y = 5
        log._max_scroll_y = 20

        ChatLog.write_token(log, "first")
        self.render_frame(log)
        self.assertEqual(0, log.scrolled)

        log._is_scroll_end = True
        log._scroll_y = 20
        ChatLog.write_token(log, " second")
        self.render_frame(log)

        self.assertTrue(log._auto_scroll)
        self.assertGreater(log.scrolled, 0)
        self.assertEqual("first second", _rendered_text(log.widgets[-1]))

    def test_context_usage_updates_even_when_value_is_zero(self):
        class FakeStatusBar:
            def __init__(self):
                self.usage = None

            def set_usage(self, value):
                self.usage = value

        status = FakeStatusBar()
        screen = ChatScreen.__new__(ChatScreen)

        def query_one(_self, _widget_type):
            return status

        screen.query_one = MethodType(query_one, screen)

        ChatScreen._update_context_usage(screen, {"context_tokens": 123})
        self.assertEqual(123, status.usage)

        ChatScreen._update_context_usage(screen, {"context_tokens": 0})
        self.assertEqual(0, status.usage)

    def test_session_switch_refreshes_status_snapshot(self):
        class FakeStatusBar:
            def __init__(self):
                self.session = None

            def set_session(self, value):
                self.session = value

        class FakeLog:
            def __init__(self):
                self.events = []

            def write_event(self, value):
                self.events.append(value)

        status = FakeStatusBar()
        log = FakeLog()
        screen = ChatScreen.__new__(ChatScreen)
        screen._session_name = "default"
        status_checked = {"value": False}

        def query_one(_self, widget_type):
            if getattr(widget_type, "__name__", "") == "StatusBar":
                return status
            return log

        async def check_status(_self):
            status_checked["value"] = True

        screen.query_one = MethodType(query_one, screen)
        screen._check_session_status = MethodType(check_status, screen)

        asyncio.run(ChatScreen._dispatch_command(screen, "/session next"))

        self.assertEqual("next", screen._session_name)
        self.assertEqual("next", status.session)
        self.assertTrue(status_checked["value"])

    def test_log_scroll_actions_work_when_input_has_focus(self):
        class FakeLog:
            def __init__(self):
                self.paused = 0
                self.page_up = 0
                self.page_down = 0
                self.home = 0
                self.bottom = 0
                self.resumed = 0

            def pause_auto_scroll(self):
                self.paused += 1

            def scroll_page_up(self, **_kwargs):
                self.page_up += 1

            def scroll_page_down(self, **_kwargs):
                self.page_down += 1

            def scroll_home(self, **_kwargs):
                self.home += 1

            def force_scroll_to_bottom(self):
                self.bottom += 1

            def _resume_if_at_bottom(self):
                self.resumed += 1

            def call_after_refresh(self, callback):
                callback()

        log = FakeLog()
        screen = ChatScreen.__new__(ChatScreen)

        def query_one(_self, _widget_type):
            return log

        screen.query_one = MethodType(query_one, screen)

        ChatScreen.action_log_page_up(screen)
        ChatScreen.action_log_page_down(screen)
        ChatScreen.action_log_home(screen)
        ChatScreen.action_log_end(screen)

        self.assertEqual(2, log.paused)
        self.assertEqual(1, log.page_up)
        self.assertEqual(1, log.page_down)
        self.assertEqual(1, log.home)
        self.assertEqual(1, log.bottom)
        self.assertEqual(1, log.resumed)

    def test_screen_mouse_scroll_pauses_log_follow_tail(self):
        class FakeLog:
            def __init__(self):
                self.paused = 0
                self.resumed = 0

            def pause_auto_scroll(self):
                self.paused += 1

            def scroll_up(self, **_kwargs):
                self.scrolled_up = getattr(self, "scrolled_up", 0) + 1

            def scroll_down(self, **_kwargs):
                self.scrolled_down = getattr(self, "scrolled_down", 0) + 1

            def _resume_if_at_bottom(self):
                self.resumed += 1

            def call_after_refresh(self, callback):
                callback()

        log = FakeLog()
        screen = ChatScreen.__new__(ChatScreen)

        def query_one(_self, _widget_type):
            return log

        screen.query_one = MethodType(query_one, screen)

        ChatScreen.on_mouse_scroll_up(screen, object())
        ChatScreen.on_mouse_scroll_down(screen, object())

        self.assertEqual(1, log.paused)
        self.assertEqual(1, log.scrolled_up)
        self.assertEqual(1, log.scrolled_down)
        self.assertEqual(1, log.resumed)

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

    def test_cancel_cancels_inflight_task_closes_client_and_releases_busy(self):
        class FakeTask:
            def __init__(self):
                self.cancelled = False

            def done(self):
                return False

            def cancel(self):
                self.cancelled = True

        class FakeClient:
            def __init__(self):
                self.closed = False

            async def close(self):
                self.closed = True

        log = self._fake_log()
        task = FakeTask()
        client = FakeClient()
        screen = ChatScreen.__new__(ChatScreen)
        screen._busy = True
        screen._inflight_task = task
        screen._inflight_client = client
        screen._streamed_response_active = True

        def query_one(_self, _widget_type):
            return log

        def run_worker(_self, awaitable, **_kwargs):
            asyncio.run(awaitable)

        screen.query_one = MethodType(query_one, screen)
        screen.run_worker = MethodType(run_worker, screen)

        ChatScreen.action_cancel(screen)

        rendered = [_rendered_text(widget) for widget in log.widgets]
        self.assertTrue(task.cancelled)
        self.assertTrue(client.closed)
        self.assertFalse(screen._busy)
        self.assertIsNone(screen._inflight_task)
        self.assertIsNone(screen._inflight_client)
        self.assertFalse(screen._streamed_response_active)
        self.assertTrue(any("Cancelled current request" in line for line in rendered))


if __name__ == "__main__":
    unittest.main()
