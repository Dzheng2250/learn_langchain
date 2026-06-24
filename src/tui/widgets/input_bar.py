"""TUI input bar — multi-line input with /command support."""

from __future__ import annotations

from textual.binding import Binding
from textual.widgets import TextArea


class InputBar(TextArea):
    """Multi-line input area with ``/`` shortcut commands.

    - ``Ctrl+Enter``: submit the current text.
    - ``Enter``: insert a newline for multi-line input.
    - ``Escape``: clear input.
    """

    BINDINGS = [
        Binding("escape", "clear", "Clear"),
    ]

    def __init__(self) -> None:
        super().__init__(
            "",
            id="input",
            language=None,
            soft_wrap=True,
        )
        self._placeholder = "Ctrl+Enter send  |  Ctrl+O tools  |  /help  |  Ctrl+D quit"

    def on_mount(self) -> None:
        self.placeholder = self._placeholder

    def action_clear(self) -> None:
        """Clear the input buffer."""
        self.text = ""

    @property
    def is_command(self) -> bool:
        """Return whether the current input starts with ``/``."""
        return self.text.strip().startswith("/")

    @property
    def command_name(self) -> str:
        """Return the command name (e.g. ``"resume"`` from ``/resume``)."""
        stripped = self.text.strip()
        if stripped.startswith("/"):
            parts = stripped[1:].split(None, 1)
            return parts[0].lower() if parts else ""
        return ""

    @property
    def command_args(self) -> str:
        """Return text after the command name."""
        stripped = self.text.strip()
        if stripped.startswith("/"):
            parts = stripped[1:].split(None, 1)
            return parts[1] if len(parts) > 1 else ""
        return ""