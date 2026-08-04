import asyncio
import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import patch

from rich.markdown import Markdown
from rich.markup import render as render_markup
from rich.text import Text

from src.tui.screens.chat import ChatScreen, _history_entries
from src.tui.renderer import render_event, render_task_progress
from src.tui.widgets.approval_bar import ApprovalBar, inline_approval_options
from src.tui.widgets.chat_log import ChatLog, HistoryEntry, _LogEntry, _ReasoningState


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



async def _completed_coroutine():
    return None

class TuiChatLogTest(unittest.TestCase):
    def test_context_compaction_progress_is_visible(self):
        rendered = render_event({
            "event": "context_compaction_progress",
            "data": {
                "stage": "map",
                "completed_groups": 2,
                "group_count": 4,
            },
        })
        self.assertIn("2/4", rendered)
        self.assertIn("map", rendered)

    def test_non_persistable_approval_hides_scoped_responses(self):
        self.assertEqual(
            ("allow_once", "deny_once"),
            inline_approval_options(False),
        )

    def test_persistable_inline_approval_exposes_session_allow_only(self):
        self.assertEqual(
            ("allow_once", "allow_session", "deny_once"),
            inline_approval_options(True),
        )

    def test_resource_activity_summary_uses_shared_core_shape(self):
        rendered = render_event({"event": "resource_activity_summary", "data": {"summary": {
            "reads": {"resource_count": 3, "returned_bytes": 64},
            "changes": {"changed_resource_count": 2},
            "evidence": {"partial": 1, "stale": 1},
        }}})
        self.assertIn("read 3", rendered)
        self.assertIn("changed 2", rendered)
        self.assertIn("warnings 2", rendered)

    def test_history_dto_uses_safe_render_modes_and_folded_details(self):
        entries = _history_entries([
            {
                "turn_index": 1,
                "messages": [
                    {
                        "role": "user",
                        "blocks": [{"type": "text", "text": "[red]literal[/red]"}],
                    },
                    {
                        "role": "assistant",
                        "blocks": [
                            {
                                "type": "reasoning",
                                "content": "private thought",
                                "char_count": 15,
                                "display": "collapsed",
                            },
                            {"type": "text", "text": "**final answer**"},
                            {
                                "type": "tool_call",
                                "id": "tool-1",
                                "name": "read_workspace_file",
                                "args": {"path": "README.md"},
                            },
                        ],
                    },
                    {
                        "role": "user",
                        "blocks": [{
                            "type": "tool_result",
                            "tool_call_id": "tool-1",
                            "name": "read_workspace_file",
                            "content": "<content omitted>",
                        }],
                    },
                ],
            }
        ])

        self.assertEqual(
            ["markup", "reasoning", "markdown", "tool", "tool"],
            [e.mode for e in entries],
        )
        self.assertIn(r"\[red]literal\[/red]", entries[0].content)
        self.assertFalse(entries[1].expanded)
        self.assertEqual("private thought", entries[1].content)
        self.assertEqual("**final answer**", entries[2].content)

    def test_history_replace_keeps_reasoning_and_tools_collapsible(self):
        log = self._fake_log()

        ChatLog.replace_history(
            log,
            [
                HistoryEntry("markup", "You: hello"),
                HistoryEntry(
                    "reasoning",
                    content="thinking",
                    char_count=8,
                    display="collapsed",
                ),
                HistoryEntry("tool", "tool detail"),
                HistoryEntry("markdown", "answer"),
            ],
            has_more=True,
        )

        self.assertTrue(log._history_has_more)
        self.assertEqual(4, len(log._entries))
        self.assertEqual(3, len(log.widgets))
        self.assertFalse(log._entries[1].reasoning.expanded)
        ChatLog.set_tool_events_visible(log, True)
        self.assertEqual(4, len(log.widgets))

    def test_history_top_posts_only_one_pagination_message(self):
        log = self._fake_log()
        log._history_has_more = True
        log._user_scroll_paused = True
        log._scroll_y = 0

        ChatLog.request_older_history_if_needed(log)
        ChatLog.request_older_history_if_needed(log)

        self.assertEqual(1, len(log.posted_messages))
        self.assertIsInstance(log.posted_messages[0], ChatLog.HistoryTopReached)

    def test_history_prepend_preserves_visible_anchor(self):
        log = self._fake_log()
        log._entries = [_LogEntry("current", "plain")]
        log._scroll_y = 20
        log._max_scroll_y = 100
        original_rebuild = log._rebuild_visible_entries

        def rebuild_with_growth(self):
            original_rebuild()
            self._max_scroll_y = 140

        log._rebuild_visible_entries = MethodType(rebuild_with_growth, log)

        ChatLog.prepend_history(
            log,
            [HistoryEntry("plain", "older")],
            has_more=False,
        )

        self.assertEqual(60, log._scroll_y)
        self.assertTrue(log._user_scroll_paused)
    def _fake_log(self):
        log = ChatLog.__new__(ChatLog)
        log._entries = []
        log._token_buf = ""
        log._active_token_widget = None
        log._task_progress_widget = None
        log._task_progress_index = None
        log._tool_events_visible = False
        log._reasoning_widget = None
        log._reasoning_index = None
        log._reasoning_target_index = None
        log._stream_committed_length = 0
        log._markdown_render_limit = 50_000
        log._partial_markdown_min_chars = 240
        log._partial_markdown_max_tail_chars = 1_200
        log._auto_scroll = True
        log._user_scroll_paused = False
        log._user_scroll_pause_until = 0.0
        log._force_follow_until = 0.0
        log._history_has_more = False
        log._history_loading = False
        log._history_top_notified = False
        log._is_scroll_end = True
        log._is_scrollbar_grabbed = False
        log._scroll_y = 0
        log._max_scroll_y = 0
        log.widgets = []
        log.scrolled = 0
        log.scrolled_up = 0
        log.scrolled_down = 0
        log.posted_messages = []

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

        def scroll_to(self, *, y, **_kwargs):
            self._scroll_y = y

        def post_message(self, message):
            self.posted_messages.append(message)

        type(log).is_vertical_scroll_end = property(lambda self: self._is_scroll_end)
        type(log).is_vertical_scrollbar_grabbed = property(
            lambda self: self._is_scrollbar_grabbed
        )
        type(log).scroll_y = property(lambda self: self._scroll_y)
        type(log).max_scroll_y = property(lambda self: self._max_scroll_y)

        def _new_widget(self, entry):
            if entry.mode in {"markup", "tool"}:
                return FakeEntryWidget(entry.content)
            if entry.mode == "reasoning":
                return FakeEntryWidget(
                    ChatLog._reasoning_markup(
                        self,
                        entry.reasoning or _ReasoningState(),
                    )
                )
            return FakeEntryWidget(ChatLog._renderable_for_entry(self, entry))

        log.mount = MethodType(mount, log)
        log.remove_children = MethodType(remove_children, log)
        def call_after_refresh(self, callback):
            callback()

        log.scroll_end = MethodType(scroll_end, log)
        log.scroll_up = MethodType(scroll_up, log)
        log.scroll_down = MethodType(scroll_down, log)
        log.scroll_to = MethodType(scroll_to, log)
        log.post_message = MethodType(post_message, log)
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



    def test_workspace_write_approval_shows_safe_operation_details(self):
        rendered = render_event({
            "event": "tool_approval_required",
            "data": {
                "tool": "write_workspace_file",
                "args": {"path": "notes.txt", "content": "private-body"},
            },
        })
        self.assertIn("Write: notes.txt", rendered)
        self.assertNotIn("private-body", rendered)
    def test_workspace_write_event_hides_content_body(self):
        rendered = render_event({
            "event": "step",
            "data": {
                "type": "tool_call_start",
                "tool": "write_workspace_file",
                "args": {"path": "notes.txt", "content": "private-body", "overwrite": False},
            },
        })
        self.assertIn("Write: notes.txt", rendered)
        self.assertNotIn("private-body", rendered)
    def test_multiline_tool_event_preserves_markup_as_one_widget(self):
        log = self._fake_log()
        markup = (
            "[bold green]\u25b6 tool: task_plan[/bold green]\n"
            "[dim]Task plan:\n"
            "  - inspect: Inspect project\n"
            "  - report: Write report[/dim]"
        )

        ChatLog.write_event(log, markup)

        self.assertEqual(1, len(log.widgets))
        self.assert_widget_texts(log, [markup])
        rendered = render_markup(log.widgets[0].renderable)
        self.assertIn("Task plan:", rendered.plain)
        self.assertIn("  - inspect: Inspect project", rendered.plain)
        self.assertIn("  - report: Write report", rendered.plain)

    def test_reasoning_block_is_collapsible_and_updates_in_place(self):
        log = self._fake_log()

        ChatLog.start_reasoning(log, expanded=False)
        ChatLog.append_reasoning(log, "hidden thought", char_count=14)
        ChatLog.finish_reasoning(log, char_count=14)

        self.assertEqual(1, len(log.widgets))
        self.assertEqual(1, len(log._entries))
        self.assertIn("Thought - 14 chars", _rendered_text(log.widgets[0]))
        self.assertNotIn("hidden thought", _rendered_text(log.widgets[0]))

        ChatLog.toggle_reasoning(log)

        self.assertEqual(1, len(log.widgets))
        self.assertIn("hidden thought", _rendered_text(log.widgets[0]))

    def test_reasoning_content_cannot_break_rich_markup(self):
        state = _ReasoningState(
            content="provider text [/[/dim] remains literal",
            char_count=40,
            expanded=True,
            finished=True,
            display="collapsed",
        )

        rendered = ChatLog._reasoning_markup(self._fake_log(), state)

        self.assertIsInstance(rendered, Text)
        self.assertIn("[/[/dim]", rendered.plain)

    def test_reasoning_deltas_render_once_on_the_next_stream_frame(self):
        log = self._fake_log()
        ChatLog.start_reasoning(log, expanded=True, display="collapsed")
        widget = log.widgets[0]
        before = _rendered_text(widget)

        ChatLog.append_reasoning(log, "first", char_count=5)
        ChatLog.append_reasoning(log, " second", char_count=12)

        self.assertEqual(before, _rendered_text(widget))
        self.render_frame(log)
        self.assertIn("first second", _rendered_text(widget))

    def test_malformed_structural_markup_falls_back_to_literal_text(self):
        markup = "[dim]broken[/[/dim]"

        rendered = ChatLog._markup_renderable(markup)

        self.assertIsInstance(rendered, Text)
        self.assertIn("broken", rendered.plain)


    def test_reasoning_metadata_expand_explains_hidden_content(self):
        log = self._fake_log()

        ChatLog.start_reasoning(log, expanded=False, display="metadata")
        ChatLog.finish_reasoning(log, char_count=42)
        ChatLog.toggle_reasoning(log)

        rendered = _rendered_text(log.widgets[0])
        self.assertIn("Thought - 42 chars", rendered)
        self.assertIn("LEARN_AGENT_REASONING_DISPLAY=metadata", rendered)

    def test_toggle_reasoning_controls_historical_blocks(self):
        log = self._fake_log()

        ChatLog.start_reasoning(log, expanded=False, display="collapsed")
        ChatLog.append_reasoning(log, "first thought", char_count=13)
        ChatLog.finish_reasoning(log, char_count=13)
        ChatLog.write_event(log, "[dim]separator[/dim]")
        ChatLog.start_reasoning(log, expanded=False, display="collapsed")
        ChatLog.append_reasoning(log, "second thought", char_count=14)
        ChatLog.finish_reasoning(log, char_count=14)

        ChatLog.toggle_reasoning(log)

        rendered = [_rendered_text(widget) for widget in log.widgets]
        self.assertTrue(any("first thought" in item for item in rendered))
        self.assertTrue(any("second thought" in item for item in rendered))

        ChatLog.toggle_reasoning(log)

        rendered = [_rendered_text(widget) for widget in log.widgets]
        self.assertFalse(any("first thought" in item for item in rendered))
        self.assertFalse(any("second thought" in item for item in rendered))

    def test_tui_reasoning_events_do_not_use_token_buffer(self):
        log = self._fake_log()
        screen = ChatScreen.__new__(ChatScreen)
        screen._streamed_response_active = False

        def query_one(_self, _widget_type):
            return log

        screen.query_one = MethodType(query_one, screen)

        async def run():
            await ChatScreen._render_event(
                screen,
                {"event": "reasoning_started", "data": {"expanded": False, "display": "collapsed"}},
            )
            await ChatScreen._render_event(
                screen,
                {
                    "event": "reasoning_delta",
                    "data": {"content": "internal", "char_count": 8},
                },
            )
            await ChatScreen._render_event(
                screen,
                {"event": "token", "data": {"content": "answer"}},
            )

        asyncio.run(run())
        self.render_frame(log)

        self.assertEqual("answer", _rendered_text(log.widgets[-1]))
        self.assertEqual("answer", log._token_buf)
        self.assertTrue(screen._streamed_response_active)
        self.assertEqual("collapsed", log._entries[0].reasoning.display)

    def test_tool_events_can_be_expanded_and_collapsed_after_storage(self):
        log = self._fake_log()

        ChatLog.write_event(log, "[bold]normal[/bold]")
        ChatLog.write_tool_event(log, "[green]▶ tool: read_file[/green]")

        self.assert_widget_texts(log, ["[bold]normal[/bold]"])
        self.assertEqual(2, len(log._entries))

        ChatLog.set_tool_events_visible(log, True)
        self.assert_widget_texts(
            log,
            ["[bold]normal[/bold]", "[green]▶ tool: read_file[/green]"],
        )

        ChatLog.set_tool_events_visible(log, False)
        self.assert_widget_texts(log, ["[bold]normal[/bold]"])

    def test_task_progress_updates_one_replaceable_widget(self):
        log = self._fake_log()
        first = render_task_progress(
            {
                "type": "tool_call_result",
                "tool": "task_update",
                "content": "Task updated: outline\n[x] outline: Outline (completed)\n[>] write: Write report (in progress)",
            }
        )
        second = render_task_progress(
            {
                "type": "tool_call_result",
                "tool": "task_update",
                "content": "Task updated: write\n[x] outline: Outline (completed)\n[x] write: Write report (completed)",
            }
        )

        ChatLog.write_task_progress(log, first)
        ChatLog.write_task_progress(log, second)

        self.assertEqual(1, len(log.widgets))
        self.assertEqual(1, len(log._entries))
        rendered = _rendered_text(log.widgets[0])
        self.assertIn("Update Todos", rendered)
        self.assertIn("outline: Outline", rendered)
        self.assertIn("write: Write report", rendered)
        self.assertNotIn("in progress", rendered)

    def test_reset_task_progress_starts_a_new_progress_block(self):
        log = self._fake_log()
        progress = render_task_progress(
            {
                "type": "tool_call_result",
                "tool": "task_list",
                "content": "[ ] outline: Outline (ready)",
            }
        )

        ChatLog.write_task_progress(log, progress)
        ChatLog.reset_task_progress(log)
        ChatLog.write_task_progress(log, progress)

        self.assertEqual(2, len(log.widgets))
        self.assertEqual(2, len(log._entries))

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
        screen._pending_approval_ids = {"approval-1", "approval-2"}
        approval_bar = SimpleNamespace(clear_request=lambda: None)

        def query_one(_self, _widget_type):
            return approval_bar if _widget_type is ApprovalBar else log

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

    def test_non_streamed_agent_message_uses_markdown_not_rich_markup(self):
        log = self._fake_log()
        screen = ChatScreen.__new__(ChatScreen)
        screen._streamed_response_active = False
        screen.query_one = MethodType(lambda _self, _widget_type: log, screen)
        content = "Model output keeps [/[/dim] and **Markdown** literal."

        asyncio.run(
            ChatScreen._on_event(
                screen,
                {
                    "event": "step",
                    "data": {"type": "agent_message", "content": content},
                },
            )
        )

        self.assertEqual([_LogEntry(content, "markdown")], log._entries)
        self.assertIsInstance(log.widgets[0].renderable, Markdown)

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

    def test_agent_start_event_clears_stale_paused_status(self):
        log = self._fake_log()
        paused_values = []
        status = SimpleNamespace(set_paused=paused_values.append)
        screen = ChatScreen.__new__(ChatScreen)
        screen._paused_execution = True
        screen._streamed_response_active = False

        def query_one(_self, widget_type):
            return status if getattr(widget_type, "__name__", "") == "StatusBar" else log

        screen.query_one = MethodType(query_one, screen)

        asyncio.run(
            ChatScreen._render_event(
                screen,
                {
                    "event": "step",
                    "data": {"type": "agent_start", "message": "Agent turn started."},
                },
            )
        )

        self.assertFalse(screen._paused_execution)
        self.assertEqual([False], paused_values)

    def test_context_usage_event_updates_status_bar_immediately(self):
        log = self._fake_log()
        usage_values = []
        status = SimpleNamespace(set_usage=usage_values.append)
        screen = ChatScreen.__new__(ChatScreen)
        screen._streamed_response_active = False
        screen.query_one = MethodType(
            lambda _self, widget_type: (
                status if getattr(widget_type, "__name__", "") == "StatusBar" else log
            ),
            screen,
        )

        asyncio.run(
            ChatScreen._render_event(
                screen,
                {
                    "event": "context_usage_updated",
                    "data": {
                        "context_tokens": 125,
                        "input_tokens": 100,
                        "output_tokens": 25,
                        "estimated": False,
                    },
                },
            )
        )

        self.assertEqual([125], usage_values)

    def test_terminal_rpc_result_clears_stale_paused_status(self):
        log = self._fake_log()
        paused_values = []
        status = SimpleNamespace(
            set_paused=paused_values.append,
            set_usage=lambda _value: None,
        )
        screen = ChatScreen.__new__(ChatScreen)
        screen._paused_execution = True
        screen.query_one = MethodType(
            lambda _self, widget_type: (
                status if getattr(widget_type, "__name__", "") == "StatusBar" else log
            ),
            screen,
        )

        ChatScreen._handle_result(screen, {"status": "ok"})

        self.assertFalse(screen._paused_execution)
        self.assertEqual([False], paused_values)

    def test_manual_resume_optimistically_clears_paused_status(self):
        paused_values = []
        status = SimpleNamespace(
            set_paused=paused_values.append,
            set_disconnected=lambda _reason: None,
        )
        log = SimpleNamespace(
            force_scroll_to_bottom=lambda: None,
            write_event=lambda _markup: None,
        )
        screen = ChatScreen.__new__(ChatScreen)
        screen._auth_token = "token"
        screen._paused_execution = True
        screen._busy = False
        screen._config = SimpleNamespace(
            core_host="127.0.0.1",
            core_port=1,
            request_timeout=1,
        )
        screen._workspace_root = "."
        screen._session_name = "default"
        screen._inflight_task = None
        screen._inflight_client = None
        observed_during_request = []

        def query_one(_self, widget_type):
            return status if getattr(widget_type, "__name__", "") == "StatusBar" else log

        screen.query_one = MethodType(query_one, screen)
        screen._on_event = lambda _event: None
        screen._wait_for_event_queue = MethodType(
            lambda _self: _completed_coroutine(),
            screen,
        )
        screen._handle_result = lambda _result: None
        screen._clear_inflight = lambda _task, _client: None

        class FakeClient:
            async def connect(self):
                return None

            async def request(self, *_args, **_kwargs):
                observed_during_request.append(screen._paused_execution)
                return {"status": "ok"}

            async def close(self):
                return None

        async def run():
            with patch("src.tui.screens.chat.AsyncCoreClient", return_value=FakeClient()):
                await ChatScreen._resume_execution(screen)

        asyncio.run(run())

        self.assertEqual([False], observed_during_request)
        self.assertFalse(screen._paused_execution)
        self.assertEqual([False], paused_values)

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

    def test_normal_chat_starts_a_new_task_progress_block(self):
        screen = ChatScreen.__new__(ChatScreen)
        screen._auth_token = "token"
        screen._busy = False
        screen._config = SimpleNamespace(
            core_host="127.0.0.1",
            core_port=1,
            request_timeout=1,
        )
        screen._workspace_root = "."
        screen._session_name = "default"
        screen._inflight_task = None
        screen._inflight_client = None
        log = SimpleNamespace(
            resets=0,
            force_scroll_to_bottom=lambda: None,
            write_event=lambda _markup: None,
        )
        log.reset_task_progress = lambda: setattr(log, "resets", log.resets + 1)
        status = SimpleNamespace(set_disconnected=lambda _reason: None)
        screen.query_one = MethodType(
            lambda _self, widget_type: log if widget_type is ChatLog else status,
            screen,
        )
        screen._on_event = lambda _event: None
        screen._wait_for_event_queue = MethodType(
            lambda _self: _completed_coroutine(),
            screen,
        )
        screen._handle_result = lambda _result: None
        screen._clear_inflight = lambda _task, _client: None

        class FakeClient:
            async def connect(self):
                return None

            async def request(self, *_args, **_kwargs):
                return {"status": "ok"}

            async def close(self):
                return None

        async def run():
            with patch("src.tui.screens.chat.AsyncCoreClient", return_value=FakeClient()):
                await ChatScreen._send_chat(screen, "hello", goal_mode=False)

        asyncio.run(run())
        self.assertEqual(1, log.resets)

    def test_tool_events_are_hidden_by_default_but_task_progress_updates(self):
        screen = ChatScreen.__new__(ChatScreen)
        screen._streamed_response_active = False
        screen._show_tool_events = False
        log = self._fake_log()

        def query_one(_self, _widget_type):
            return log

        screen.query_one = MethodType(query_one, screen)

        async def run():
            await ChatScreen._render_event(
                screen,
                {
                    "event": "step",
                    "data": {
                        "type": "tool_call_result",
                        "tool": "task_update",
                        "content": "Task updated: outline\n[x] outline: Outline (completed)",
                    },
                },
            )
            await ChatScreen._render_event(
                screen,
                {
                    "event": "step",
                    "data": {
                        "type": "tool_call_start",
                        "tool": "read_file",
                        "args": {"path": "README.md"},
                    },
                },
            )

        asyncio.run(run())

        self.assertEqual(1, len(log.widgets))
        rendered = render_markup(log.widgets[0].renderable)
        self.assertIn("Update Todos", rendered.plain)
        self.assertNotIn("read_file", rendered.plain)

    def test_toggle_tool_events_allows_verbose_tool_log_without_note_noise(self):
        screen = ChatScreen.__new__(ChatScreen)
        screen._streamed_response_active = False
        screen._show_tool_events = False
        log = self._fake_log()

        def query_one(_self, _widget_type):
            return log

        screen.query_one = MethodType(query_one, screen)

        ChatScreen.action_toggle_tool_events(screen)

        async def run():
            await ChatScreen._render_event(
                screen,
                {
                    "event": "step",
                    "data": {
                        "type": "tool_call_start",
                        "tool": "read_file",
                        "args": {"path": "README.md"},
                    },
                },
            )

        asyncio.run(run())

        self.assertTrue(screen._show_tool_events)
        self.assertEqual(1, len(log.widgets))
        self.assertIn("read_file", _rendered_text(log.widgets[0]))
        self.assertFalse(any("Tool execution details" in _rendered_text(widget) for widget in log.widgets))

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
        approval_bar = SimpleNamespace(clear_request=lambda: None)
        screen = ChatScreen.__new__(ChatScreen)
        screen._session_name = "default"
        screen._busy = False
        loaded_session = {"value": None}

        def query_one(_self, widget_type):
            if getattr(widget_type, "__name__", "") == "StatusBar":
                return status
            if widget_type is ApprovalBar:
                return approval_bar
            return log

        async def load_session(_self, session_name):
            loaded_session["value"] = session_name

        screen.query_one = MethodType(query_one, screen)
        screen._load_session_view = MethodType(load_session, screen)

        asyncio.run(ChatScreen._dispatch_command(screen, "/session next"))

        self.assertEqual("next", screen._session_name)
        self.assertEqual("next", status.session)
        self.assertEqual("next", loaded_session["value"])

    def test_session_switch_is_rejected_while_agent_request_is_running(self):
        log = self._fake_log()
        status = SimpleNamespace(set_session=lambda _value: None)
        screen = ChatScreen.__new__(ChatScreen)
        screen._session_name = "default"
        screen._busy = True

        def query_one(_self, widget_type):
            return status if getattr(widget_type, "__name__", "") == "StatusBar" else log

        screen.query_one = MethodType(query_one, screen)

        asyncio.run(ChatScreen._dispatch_command(screen, "/session next"))

        self.assertEqual("default", screen._session_name)
        self.assertIn("Cannot switch Session", str(log._entries[-1].content))

    def test_late_history_response_cannot_replace_new_session_view(self):
        class FakeLog:
            def __init__(self):
                self.pages = []

            def clear(self):
                return None

            def replace_history(self, entries, *, has_more):
                self.pages.append(([entry.content for entry in entries], has_more))

            def write_event(self, _value):
                return None

        class FakeStatus:
            def set_paused(self, _value):
                return None

            def set_goal_mode(self, _value):
                return None

        async def scenario():
            log = FakeLog()
            status = FakeStatus()
            approval = SimpleNamespace(clear_request=lambda: None)
            screen = ChatScreen.__new__(ChatScreen)
            screen._session_name = "first"
            screen._history_generation = 0
            screen._history_before_turn = None
            screen._history_has_more = False
            screen._history_loading = False
            screen._streamed_response_active = False
            screen._show_tool_events = True
            screen._goal_mode = True
            screen._paused_execution = False
            screen._pending_approval_ids = set()
            screen._pending_approval_requests = {}
            screen._resolving_approval_ids = set()
            first_release = asyncio.Event()

            def query_one(_self, widget_type):
                name = getattr(widget_type, "__name__", "")
                if name == "StatusBar":
                    return status
                if widget_type is ApprovalBar:
                    return approval
                return log

            async def request_history(_self, session_name, *, before_turn):
                if session_name == "first":
                    await first_release.wait()
                return {
                    "turns": [{
                        "turn_index": 1,
                        "messages": [{
                            "role": "assistant",
                            "blocks": [{"type": "text", "text": session_name}],
                        }],
                    }],
                    "next_before_turn": None,
                    "has_more": False,
                }

            async def check_status(_self, **_kwargs):
                return None

            screen.query_one = MethodType(query_one, screen)
            screen._request_session_history = MethodType(request_history, screen)
            screen._check_session_status = MethodType(check_status, screen)

            first = asyncio.create_task(ChatScreen._load_session_view(screen, "first"))
            await asyncio.sleep(0)
            screen._session_name = "second"
            await ChatScreen._load_session_view(screen, "second")
            first_release.set()
            await first
            return log.pages, screen

        pages, screen = asyncio.run(scenario())

        self.assertEqual([(["second"], False)], pages)
        self.assertFalse(screen._show_tool_events)
        self.assertFalse(screen._goal_mode)

    def test_log_scroll_actions_work_when_input_has_focus(self):
        class FakeLog:
            def __init__(self):
                self.paused = 0
                self.page_up = 0
                self.page_down = 0
                self.home = 0
                self.bottom = 0
                self.resumed = 0
                self.history_requests = 0

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

            def request_older_history_if_needed(self):
                self.history_requests += 1

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
        self.assertEqual(2, log.history_requests)

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
        screen._pending_approval_ids = {"approval-1", "approval-2"}
        approval_bar = SimpleNamespace(clear_request=lambda: None)

        def query_one(_self, _widget_type):
            return approval_bar if _widget_type is ApprovalBar else log

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
        self.assertEqual(set(), screen._pending_approval_ids)
        self.assertTrue(any("Cancelled current request" in line for line in rendered))


    def test_error_event_escapes_rich_markup_from_exception_text(self):
        from rich.markup import render

        rendered = render_event({
            "event": "error",
            "data": {"message": "bad {'messages': [HumanMessage()]}"},
        })

        self.assertIn("bad {'messages': [HumanMessage()]}", render(rendered).plain)
    def test_tool_validation_error_escapes_rich_markup(self):
        rendered = render_event({
            "event": "step",
            "data": {
                "type": "tool_call_result",
                "tool": "task_update",
                "content": (
                    "ValidationError: status\n"
                    "Input should be valid [type=literal_error, "
                    "input_value=None, input_type=NoneType]"
                ),
            },
        })

        parsed = render_markup(rendered)
        self.assertIn("input_value=None", parsed.plain)

    def test_goal_continuation_event_is_visible(self):
        rendered = render_event({
            "event": "goal_continuation_started",
            "data": {"slice_number": 1},
        })

        self.assertIn("checking unfinished tasks", rendered)
if __name__ == "__main__":
    unittest.main()
