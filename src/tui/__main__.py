"""python -m src.tui — launch the TUI client."""

from __future__ import annotations

import sys

from src.tui.app import TuiApp
from src.tui.config import TuiConfig


def main() -> int:
    """Launch the TUI application."""
    config = TuiConfig()
    app = TuiApp(config)
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())