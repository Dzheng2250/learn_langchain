"""Launch the TUI (terminal UI) client."""

from __future__ import annotations

import sys

from src.tui.app import TuiApp
from src.tui.config import TuiConfig


def register(subparsers, _config) -> None:
    parser = subparsers.add_parser("tui", help="launch the terminal UI client")
    parser.set_defaults(handler=run)


def run(_args, _config) -> int:
    """Start the TUI application."""
    config = TuiConfig()
    app = TuiApp(config)
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    return 0