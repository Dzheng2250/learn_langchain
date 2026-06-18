"""TUI application — entry point for the learn-agent Textual TUI."""

from __future__ import annotations

from textual.app import App

from src.tui.config import TuiConfig
from src.tui.screens.chat import ChatScreen


class TuiApp(App):
    """Learn-agent TUI application."""

    TITLE = "Learn Agent TUI"
    CSS = """
    Screen {
        background: $surface;
    }
    """

    def __init__(self, config: TuiConfig | None = None) -> None:
        super().__init__()
        self._config = config or TuiConfig()

    def on_mount(self) -> None:
        self.push_screen(ChatScreen(self._config))