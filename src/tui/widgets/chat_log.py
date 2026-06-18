"""TUI chat log — event stream display with auto-scroll."""

from __future__ import annotations

from textual.widgets import RichLog


class ChatLog(RichLog):
    """Event stream display with auto-scroll to bottom."""

    def __init__(self) -> None:
        super().__init__(
            id="log",
            highlight=True,
            markup=True,
            min_width=40,
            wrap=True,
        )
        self._entries: list[str] = []
        self._token_buf: str = ""

    def write_token(self, content: str) -> None:
        """Append a token chunk and refresh the visible streamed response."""
        self._token_buf += content
        self._render_token_buffer()

    def flush_tokens(self) -> None:
        """Finish the current streamed response before writing another event."""
        if self._token_buf:
            self._entries.append(self._token_buf)
            self._token_buf = ""
            self._redraw()

    def write_event(self, markup: str) -> None:
        """Flush any pending tokens, then write a (possibly multi-line) event."""
        self.flush_tokens()
        for line in markup.split("\n"):
            if line:
                self._entries.append(line)
        self._redraw()

    def _render_token_buffer(self) -> None:
        """Render committed log entries plus the current streamed response.

        ``RichLog.write`` always appends a new entry. Updating ``lines`` in
        place is fragile because RichLog may split one renderable into multiple
        visual lines. Keep our own committed entries and redraw the log with the
        in-flight token buffer as the final entry, so token chunks do not become
        independent rows.
        """
        if not self._token_buf or not self._size_known:
            return
        self._redraw(include_token=True)

    def _redraw(self, *, include_token: bool = False) -> None:
        """Redraw all committed entries and optionally the active token buffer."""
        if not self._size_known:
            return
        self.clear()
        for entry in self._entries:
            self.write(entry)
        if include_token and self._token_buf:
            self.write(self._token_buf)
        self.refresh()
