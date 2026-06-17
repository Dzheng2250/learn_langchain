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
        self._buffer: list[str] = []
        self._token_buf: str = ""
        self._token_line_start: int | None = None

    def write_token(self, content: str) -> None:
        """Append a token chunk and refresh the visible streamed response."""
        self._token_buf += content
        self._render_token_buffer()

    def flush_tokens(self) -> None:
        """Finish the current streamed response before writing another event."""
        if self._token_buf:
            if self._token_line_start is None:
                self.write(self._token_buf)
            self._token_buf = ""
            self._token_line_start = None

    def write_event(self, markup: str) -> None:
        """Flush any pending tokens, then write a (possibly multi-line) event."""
        self.flush_tokens()
        for line in markup.split("\n"):
            if line:
                self.write(line)

    def _render_token_buffer(self) -> None:
        """Rewrite the current streamed response in place.

        ``RichLog.write`` appends a new rendered entry; it cannot append text to
        the previous entry. To keep token streaming visible without producing one
        log row per token, remember where the current AI response started, remove
        the previous rendered version, then write the accumulated text again.
        """
        if not self._token_buf or not self._size_known:
            return
        if self._token_line_start is None:
            self._token_line_start = len(self.lines)
        else:
            del self.lines[self._token_line_start :]
        self.write(self._token_buf)
        self.refresh()
