"""TUI chat log — event stream display with auto-scroll."""

from __future__ import annotations

import time
from dataclasses import dataclass

from rich.markdown import Markdown
from rich.markup import escape
from rich.text import Text
from textual import events
from textual.containers import VerticalScroll
from textual.widgets import Static


MARKDOWN_RENDER_LIMIT = 50_000
PARTIAL_MARKDOWN_MIN_CHARS = 240
PARTIAL_MARKDOWN_MAX_TAIL_CHARS = 1_200
STREAM_RENDER_INTERVAL_SECONDS = 1 / 30
USER_SCROLL_PAUSE_SECONDS = 1.5
FORCED_FOLLOW_SECONDS = 0.75


@dataclass
class _ReasoningState:
    """Mutable display state for one reasoning/thinking block."""

    content: str = ""
    char_count: int = 0
    redacted: bool = False
    expanded: bool = False
    finished: bool = False
    display: str = "metadata"


@dataclass(frozen=True)
class _LogEntry:
    """A committed visual log entry.

    ``mode`` controls how the entry is rendered:
    - ``markup``: trusted TUI markup produced by ``src.tui.renderer``.
    - ``plain``: literal text; Rich markup is not interpreted.
    - ``tool``: trusted Rich markup for collapsible tool execution details.
    - ``reasoning``: model thinking block with per-entry fold state.
    - ``markdown``: completed assistant answer rendered once with Markdown.
    """

    content: str
    mode: str = "markup"
    reasoning: _ReasoningState | None = None


class ChatLog(VerticalScroll):
    """Append-only event log with an incrementally updated stream block.

    A ``RichLog`` can append lines efficiently, but it has no stable public API for
    replacing the last written entry. The previous implementation preserved line
    layout by clearing and replaying the entire log for every token, which made
    long answers slow and also hid intermediate rendering in token bursts.

    This widget keeps each committed entry as a child ``Static`` widget. Streaming
    tokens update the active tail widget immediately, while stable paragraph-sized
    prefixes are periodically committed as Markdown widgets. The final tail is
    rendered once when the response completes.
    """

    def __init__(self) -> None:
        super().__init__(id="log")
        self._entries: list[_LogEntry] = []
        self._token_buf: str = ""
        self._active_token_widget: Static | None = None
        self._task_progress_widget: Static | None = None
        self._task_progress_index: int | None = None
        self._tool_events_visible: bool = False
        self._reasoning_widget: Static | None = None
        self._reasoning_index: int | None = None
        self._reasoning_target_index: int | None = None
        self._stream_committed_length: int = 0
        self._markdown_render_limit: int = MARKDOWN_RENDER_LIMIT
        self._partial_markdown_min_chars: int = PARTIAL_MARKDOWN_MIN_CHARS
        self._partial_markdown_max_tail_chars: int = PARTIAL_MARKDOWN_MAX_TAIL_CHARS
        self._auto_scroll: bool = True
        self._user_scroll_paused: bool = False
        self._user_scroll_pause_until: float = 0.0
        self._force_follow_until: float = 0.0

    def on_mount(self) -> None:
        """Start a fixed-rate render pump for buffered stream tokens."""
        self.set_interval(STREAM_RENDER_INTERVAL_SECONDS, self.render_pending_tokens)

    def write_token(self, content: str) -> None:
        """Append a token chunk without forcing an immediate layout pass."""
        self._token_buf += content

    def render_pending_tokens(self) -> None:
        """Flush buffered token chunks at UI frame rate.

        IPC may deliver many token notifications back-to-back. Rendering each one
        synchronously starves Textual's mouse and scroll events, so the widget
        batches token chunks and updates the visible stream at a bounded rate.
        """
        self._capture_scroll_follow_state()
        self._render_token_buffer()

    def flush_tokens(self) -> None:
        """Finish the current streamed response before writing another event."""
        if not self._token_buf:
            return
        self.render_pending_tokens()
        self._capture_scroll_follow_state()
        self._commit_stream_prefix(len(self._token_buf))
        self._active_token_widget = None
        self._token_buf = ""
        self._stream_committed_length = 0
        self._scroll_to_bottom()

    def write_event(self, markup: str) -> None:
        """Flush any pending tokens, then write a TUI markup event."""
        self.flush_tokens()
        self._capture_scroll_follow_state()
        if markup.strip():
            self._append_committed(_LogEntry(markup, "markup"))

    def write_tool_event(self, markup: str) -> None:
        """Store one collapsible tool event and mount it only when expanded."""
        self.flush_tokens()
        self._capture_scroll_follow_state()
        if not markup.strip():
            return
        entry = _LogEntry(markup, "tool")
        self._entries.append(entry)
        if self._tool_events_visible:
            self._append_entry(entry)
            self._scroll_to_bottom()

    def set_tool_events_visible(self, visible: bool) -> None:
        """Expand or collapse stored tool execution details."""
        if self._tool_events_visible == visible:
            return
        self.flush_tokens()
        self._capture_scroll_follow_state()
        self._tool_events_visible = visible
        self._rebuild_visible_entries()
        self._scroll_to_bottom()

    def start_reasoning(self, *, expanded: bool = False, display: str = "metadata") -> None:
        """Start a replaceable reasoning/thinking block."""
        self.flush_tokens()
        self._capture_scroll_follow_state()
        state = _ReasoningState(expanded=expanded, display=display or "metadata")
        entry = _LogEntry("", "reasoning", reasoning=state)
        self._entries.append(entry)
        self._reasoning_index = len(self._entries) - 1
        self._reasoning_target_index = self._reasoning_index
        self._reasoning_widget = self._append_entry(entry)
        self._scroll_to_bottom()

    def append_reasoning(
        self,
        content: str = "",
        *,
        char_count: int = 0,
        redacted: bool = False,
    ) -> None:
        """Update the active reasoning block without touching answer tokens."""
        entry, widget, index = self._active_reasoning_entry()
        if entry is None or widget is None or index is None:
            self.start_reasoning(expanded=False)
            entry, widget, index = self._active_reasoning_entry()
        if entry is None or widget is None or index is None or entry.reasoning is None:
            return
        state = entry.reasoning
        if content:
            state.content += content
        state.char_count = max(state.char_count, int(char_count or 0))
        state.redacted = state.redacted or redacted
        self._replace_reasoning_entry(index, widget, state)

    def finish_reasoning(self, *, char_count: int = 0, redacted: bool = False) -> None:
        """Mark the active reasoning block complete."""
        entry, widget, index = self._active_reasoning_entry()
        if entry is None or widget is None or index is None:
            self.start_reasoning(expanded=False)
            entry, widget, index = self._active_reasoning_entry()
        if entry is None or widget is None or index is None or entry.reasoning is None:
            return
        state = entry.reasoning
        state.char_count = max(state.char_count, int(char_count or 0))
        state.redacted = state.redacted or redacted
        state.finished = True
        self._replace_reasoning_entry(index, widget, state)

    def toggle_reasoning(self) -> None:
        """Expand or collapse every reasoning block, including history."""
        indexes = [
            index
            for index, entry in enumerate(self._entries)
            if entry.mode == "reasoning" and entry.reasoning is not None
        ]
        if not indexes:
            return
        expand = any(not self._entries[index].reasoning.expanded for index in indexes)
        for index in indexes:
            entry = self._entries[index]
            if entry.reasoning is None:
                continue
            entry.reasoning.expanded = expand
            widget = self._widget_for_entry_index(index)
            if widget is not None:
                self._replace_reasoning_entry(index, widget, entry.reasoning, follow=False)
        self._reasoning_target_index = indexes[-1]

    def _active_reasoning_entry(self):
        """Return the current stream reasoning entry and mounted widget."""
        if self._reasoning_index is None or self._reasoning_widget is None:
            return None, None, None
        if not self._is_reasoning_index(self._reasoning_index):
            return None, None, None
        return self._entries[self._reasoning_index], self._reasoning_widget, self._reasoning_index

    def _is_reasoning_index(self, index: int) -> bool:
        return 0 <= index < len(self._entries) and self._entries[index].mode == "reasoning"

    def _widget_for_entry_index(self, target_index: int):
        """Find the mounted widget corresponding to an entry index."""
        visible_index = 0
        for index, entry in enumerate(self._entries):
            if entry.mode == "tool" and not self._tool_events_visible:
                continue
            if index == target_index:
                children = list(getattr(self, "children", []))
                if not children:
                    children = list(getattr(self, "widgets", []))
                if 0 <= visible_index < len(children):
                    widget = children[visible_index]
                    if isinstance(widget, Static) or hasattr(widget, "update"):
                        return widget
                return None
            visible_index += 1
        return None

    def _replace_reasoning_entry(
        self,
        index: int,
        widget,
        state: _ReasoningState,
        *,
        follow: bool = True,
    ) -> None:
        """Replace one reasoning entry in place."""
        entry = _LogEntry("", "reasoning", reasoning=state)
        self._entries[index] = entry
        self._update_widget(widget, entry)
        if follow:
            self._scroll_to_bottom()

    def _reasoning_markup(self, state: _ReasoningState) -> str:
        """Render one reasoning block as collapsible Rich markup."""
        count = state.char_count or len(state.content)
        label = "Thought" if state.finished else "Thinking"
        suffix = f" - {count} chars" if count else ""
        if state.redacted:
            suffix += " - redacted"
        toggle = "[-]" if state.expanded else "[+]"
        header = f"[dim]{toggle} {label}{suffix}[/dim]"
        if not state.expanded:
            return header
        if state.content:
            return f"{header}\n[dim]{escape(state.content)}[/dim]"
        if state.redacted:
            reason = "Provider returned redacted thinking; raw content is unavailable."
        elif state.display in {"metadata", "hidden"}:
            reason = (
                "Thinking text is hidden by LEARN_AGENT_REASONING_DISPLAY="
                f"{escape(state.display)}."
            )
        else:
            reason = "No reasoning text was provided by the model stream."
        return f"{header}\n[dim]{reason}[/dim]"
    def write_task_progress(self, markup: str) -> None:
        """Create or replace the latest visible private-task progress block."""
        self.flush_tokens()
        self._capture_scroll_follow_state()
        if not markup.strip():
            return
        entry = _LogEntry(markup, "markup")
        if self._task_progress_widget is None or self._task_progress_index is None:
            self._entries.append(entry)
            self._task_progress_index = len(self._entries) - 1
            self._task_progress_widget = self._append_entry(entry)
        else:
            self._entries[self._task_progress_index] = entry
            self._update_widget(self._task_progress_widget, entry)
        self._scroll_to_bottom()

    def reset_task_progress(self) -> None:
        """Start a new task-progress block for the next goal Execution."""
        self._task_progress_widget = None
        self._task_progress_index = None

    def mark_tokens_stale(self, reason: str) -> None:
        """Keep a failed draft for diagnosis but mark it as non-authoritative."""
        self.render_pending_tokens()
        self._capture_scroll_follow_state()
        if self._token_buf:
            self._append_committed(_LogEntry("[dim]INCOMPLETE MODEL DRAFT (STALE)[/dim]"))
            if self._stream_committed_length < len(self._token_buf):
                draft = _LogEntry(self._token_buf[self._stream_committed_length :], "plain")
                self._entries.append(draft)
                if self._active_token_widget is None:
                    self._append_entry(draft)
                else:
                    self._update_widget(self._active_token_widget, draft)
                    self._active_token_widget = None
            self._token_buf = ""
            self._stream_committed_length = 0
        self._append_committed(_LogEntry(f"[yellow]{reason}[/yellow]"))

    def clear(self) -> None:  # type: ignore[override]
        """Clear visual log state and mounted log entry widgets."""
        self._entries.clear()
        self._token_buf = ""
        self._active_token_widget = None
        self._task_progress_widget = None
        self._task_progress_index = None
        self._tool_events_visible = False
        self._reasoning_widget = None
        self._reasoning_index = None
        self._reasoning_target_index = None
        self._stream_committed_length = 0
        self._auto_scroll = True
        self._user_scroll_paused = False
        self._user_scroll_pause_until = 0.0
        self._force_follow_until = 0.0
        self.remove_children()



    def pause_auto_scroll(self) -> None:
        """Let user navigation take priority over streaming follow-tail."""
        self._user_scroll_paused = True
        self._auto_scroll = False
        self._user_scroll_pause_until = time.monotonic() + USER_SCROLL_PAUSE_SECONDS
        self._force_follow_until = 0.0

    def resume_auto_scroll(self) -> None:
        """Resume follow-tail after the user returns to the live output."""
        self._user_scroll_paused = False
        self._auto_scroll = True
        self._user_scroll_pause_until = 0.0
        self._force_follow_until = 0.0
        self._scroll_to_bottom()

    def force_scroll_to_bottom(self) -> None:
        """Move to the latest output and make future stream chunks follow it."""
        self._user_scroll_paused = False
        self._auto_scroll = True
        self._user_scroll_pause_until = 0.0
        self._force_follow_until = time.monotonic() + FORCED_FOLLOW_SECONDS
        try:
            self.scroll_end(animate=False)
        except Exception:
            pass

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        """Observe real scroll movement without replacing Textual's scroll logic."""
        super().watch_scroll_y(old_value, new_value)
        try:
            old_scroll = float(old_value)
            new_scroll = float(new_value)
        except (TypeError, ValueError):
            return
        if new_scroll < old_scroll:
            self.pause_auto_scroll()
            return
        if self._user_scroll_paused and self._is_at_vertical_end():
            self._user_scroll_paused = False
            self._auto_scroll = True
            self._user_scroll_pause_until = 0.0
            self._force_follow_until = 0.0

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        """Mouse-wheel upward means the user wants to inspect history."""
        self.pause_auto_scroll()
        try:
            self._on_mouse_scroll_up(event)
        except Exception:
            self.scroll_up(animate=False)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        """Scroll down normally and resume follow-tail only at the bottom."""
        try:
            self._on_mouse_scroll_down(event)
        except Exception:
            self.scroll_down(animate=False)
        try:
            self.call_after_refresh(self._resume_if_at_bottom)
        except Exception:
            self._resume_if_at_bottom()

    def _resume_if_at_bottom(self) -> None:
        """Resume auto-follow when user-controlled scrolling reaches the bottom."""
        if self._is_at_vertical_end():
            self._user_scroll_paused = False
            self._auto_scroll = True
            self._user_scroll_pause_until = 0.0
            self._force_follow_until = 0.0

    def _render_token_buffer(self) -> None:
        """Render staged Markdown blocks plus the current literal tail.

        Stable completed blocks are periodically committed as Markdown while the
        still-changing tail remains plain text. This keeps long responses readable
        before the final answer arrives without parsing Markdown on every token.
        """
        if not self._token_buf:
            return
        boundary = self._next_stage_boundary()
        if boundary is not None:
            self._commit_stream_prefix(boundary)
        self._render_stream_tail()
        self._scroll_to_bottom()

    def _next_stage_boundary(self) -> int | None:
        """Return a safe prefix length to stage-render as Markdown, if any."""
        tail_length = len(self._token_buf) - self._stream_committed_length
        if tail_length < self._partial_markdown_min_chars:
            return None
        search_from = self._stream_committed_length + self._partial_markdown_min_chars
        paragraph = self._token_buf.rfind("\n\n", search_from)
        if paragraph != -1:
            boundary = paragraph + 2
            if self._is_safe_markdown_boundary(boundary):
                return boundary
        if tail_length >= self._partial_markdown_max_tail_chars:
            boundary = self._token_buf.rfind("\n", self._stream_committed_length + 1)
            if boundary != -1 and boundary > self._stream_committed_length:
                boundary += 1
            else:
                boundary = min(
                    len(self._token_buf),
                    self._stream_committed_length + self._partial_markdown_max_tail_chars,
                )
            if self._is_safe_markdown_boundary(boundary):
                return boundary
        return None

    def _is_safe_markdown_boundary(self, boundary: int) -> bool:
        """Avoid staging a Markdown block in the middle of a fenced code block."""
        segment = self._token_buf[self._stream_committed_length : boundary]
        return segment.count("```") % 2 == 0

    def _commit_stream_prefix(self, boundary: int) -> None:
        """Commit stream content up to ``boundary`` as Markdown."""
        if boundary <= self._stream_committed_length:
            return
        content = self._token_buf[self._stream_committed_length : boundary]
        entry = _LogEntry(content, "markdown")
        self._entries.append(entry)
        if self._active_token_widget is None:
            self._append_entry(entry)
        else:
            self._update_widget(self._active_token_widget, entry)
            self._active_token_widget = None
        self._stream_committed_length = boundary

    def _render_stream_tail(self) -> None:
        """Render the currently unstable tail as literal text."""
        tail = self._token_buf[self._stream_committed_length :]
        if not tail:
            self._active_token_widget = None
            return
        entry = _LogEntry(tail, "plain")
        if self._active_token_widget is None:
            self._active_token_widget = self._append_entry(entry)
        else:
            self._update_widget(self._active_token_widget, entry)

    def _append_committed(self, entry: _LogEntry) -> Static:
        """Store and mount a committed entry."""
        self._entries.append(entry)
        widget = self._append_entry(entry)
        self._scroll_to_bottom()
        return widget

    def _append_entry(self, entry: _LogEntry) -> Static:
        """Create and mount one entry widget."""
        widget = self._new_widget(entry)
        self.mount(widget)
        return widget

    def _rebuild_visible_entries(self) -> None:
        """Recreate mounted widgets after a visibility-only display change."""
        self.remove_children()
        self._active_token_widget = None
        self._task_progress_widget = None
        for index, entry in enumerate(self._entries):
            if entry.mode == "tool" and not self._tool_events_visible:
                continue
            widget = self._append_entry(entry)
            if index == self._task_progress_index:
                self._task_progress_widget = widget
            if index == self._reasoning_index:
                self._reasoning_widget = widget
            if index == self._reasoning_target_index and entry.mode == "reasoning":
                self._reasoning_target_index = index

    def _new_widget(self, entry: _LogEntry) -> Static:
        """Create a Textual widget for one log entry.

        ``markup`` entries are generated by the TUI renderer and may use Rich
        markup. Plain stream entries are rendered as ``Text`` so brackets and
        whitespace are preserved. Completed answers use Markdown unless they are
        too large for a single safe render pass.
        """
        if entry.mode in {"markup", "tool"}:
            return Static(entry.content, markup=True, classes=f"chat-log-entry chat-log-{entry.mode}")
        if entry.mode == "reasoning":
            markup = self._reasoning_markup(entry.reasoning or _ReasoningState())
            return Static(markup, markup=True, classes="chat-log-entry chat-log-reasoning")
        return Static(
            self._renderable_for_entry(entry),
            markup=False,
            classes=f"chat-log-entry chat-log-{entry.mode}",
        )

    def _update_widget(self, widget: Static, entry: _LogEntry) -> None:
        """Update an existing entry widget in place."""
        if entry.mode in {"markup", "tool"}:
            widget.update(entry.content)
            return
        if entry.mode == "reasoning":
            widget.update(self._reasoning_markup(entry.reasoning or _ReasoningState()))
            return
        widget.update(self._renderable_for_entry(entry))

    def _renderable_for_entry(self, entry: _LogEntry):
        """Return the Rich renderable for non-markup entries."""
        if entry.mode == "markdown" and len(entry.content) <= self._markdown_render_limit:
            return Markdown(entry.content)
        return Text(entry.content)

    def _is_force_follow_active(self) -> bool:
        """Return whether a new user action requested temporary follow-tail."""
        return time.monotonic() < self._force_follow_until

    def _capture_scroll_follow_state(self) -> None:
        """Remember whether new output should keep following the bottom.

        ChatLog must not override Textual's scroll actions or watchers. It only
        samples the current position before appending content. If user scrolling
        has paused follow-tail, token frames must not clear that pause merely
        because layout still reports the previous bottom position.
        """
        try:
            if self._is_force_follow_active():
                self._user_scroll_paused = False
                self._auto_scroll = True
                return
            if self.is_vertical_scrollbar_grabbed:
                self.pause_auto_scroll()
                return
            if self._user_scroll_paused:
                self._auto_scroll = False
                return
            self._auto_scroll = self._is_at_vertical_end()
        except Exception:
            # Unit tests and early mount states may not have a full scroll model.
            self._auto_scroll = True

    def _is_at_vertical_end(self) -> bool:
        """Return whether the viewport is effectively at the current bottom.

        Prefer the numeric position over ``is_vertical_scroll_end``. In the full
        ChatScreen layout Textual may report the boolean end flag before layout
        has fully settled, while ``scroll_y`` and ``max_scroll_y`` still show the
        real distance from the bottom.
        """
        try:
            max_scroll = float(self.max_scroll_y)
            if max_scroll <= 0:
                return True
            return float(self.scroll_y) >= max_scroll - 1
        except Exception:
            try:
                return bool(self.is_vertical_scroll_end)
            except Exception:
                return True

    def _scroll_to_bottom(self) -> None:
        """Keep latest content visible only while the user is following output."""
        if not self._is_force_follow_active() and time.monotonic() < self._user_scroll_pause_until:
            self._auto_scroll = False
            return
        if not self._auto_scroll:
            return
        try:
            self.scroll_end(animate=False)
        except Exception:
            # Unit tests instantiate ChatLog without a mounted Textual app.
            pass
