"""TUI status bar — connection state, session, daemon info."""

from __future__ import annotations

from typing import Literal

from src.config.settings import MODEL_CONTEXT_LIMIT

from rich.text import Text
from textual.widgets import Label

ConnectionState = Literal["connecting", "connected", "disconnected", "error"]


class StatusBar(Label):
    """Single-line status bar showing daemon connection and session state."""

    def __init__(self) -> None:
        super().__init__("")
        self._state: ConnectionState = "connecting"
        self._host = ""
        self._port = 0
        self._session = "default"
        self._goal_mode = False
        self._paused = False
        self._context_tokens: int = 0
        self._context_limit: int = MODEL_CONTEXT_LIMIT

    def set_connecting(self, host: str, port: int) -> None:
        self._state = "connecting"
        self._host = host
        self._port = port
        self._refresh()

    def set_connected(self, host: str, port: int) -> None:
        self._state = "connected"
        self._host = host
        self._port = port
        self._refresh()

    def set_disconnected(self, reason: str = "") -> None:
        self._state = "disconnected"
        self._refresh(reason)

    def set_error(self, message: str) -> None:
        self._state = "error"
        self._refresh(message)

    def set_session(self, name: str) -> None:
        self._session = name
        self._refresh()

    def set_goal_mode(self, enabled: bool) -> None:
        self._goal_mode = enabled
        self._refresh()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self._refresh()

    def set_usage(self, context_tokens: int) -> None:
        self._context_tokens = context_tokens
        self._refresh()

    def _refresh(self, extra: str = "") -> None:
        text = Text()
        dot = {
            "connecting": Text(" ● ", style="bold yellow"),
            "connected": Text(" ● ", style="bold green"),
            "disconnected": Text(" ● ", style="bold red"),
            "error": Text(" ● ", style="bold red"),
        }
        text.append_text(dot.get(self._state, Text(" ? ")))

        if self._state == "connecting":
            text.append(f"connecting {self._host}:{self._port}...")
        elif self._state == "connected":
            text.append(f"{self._host}:{self._port} ")
            text.append(Text(f"[{self._session}]", style="dim"))
            if self._context_tokens > 0:
                pct = min(100, self._context_tokens * 100 // self._context_limit)
                text.append(Text(f" ctx: {self._context_tokens // 1000}K/{self._context_limit // 1000}K ({pct}%)", style="dim"))
            if self._goal_mode:
                text.append(Text(" goal", style="bold cyan"))
            if self._paused:
                text.append(Text(" paused", style="bold yellow"))
        elif self._state == "disconnected":
            text.append(f"disconnected {self._host}:{self._port}")
            if extra:
                text.append(Text(f" — {extra}", style="dim"))
        elif self._state == "error":
            text.append(Text(f"error: {extra or 'unknown'}", style="bold"))

        self.update(text)