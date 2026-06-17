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

    def write_token(self, content: str) -> None:
        """Accumulate a token chunk; flushed by :meth:`flush_tokens`."""
        self._token_buf += content

    def flush_tokens(self) -> None:
        """Write accumulated tokens as a single log entry."""
        if self._token_buf:
            self.write(self._token_buf)
            self._token_buf = ""

    def write_event(self, markup: str) -> None:
        """Flush any pending tokens, then write a (possibly multi-line) event."""
        self.flush_tokens()
        for line in markup.split("\n"):
            if line:
                self.write(line)