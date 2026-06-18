"""CLI command registration."""

from .chat import register as register_chat
from .start import register as register_start
from .status import register as register_status
from .stop import register as register_stop
from .session import register as register_session
from .trace import register as register_trace
from .tui import register as register_tui


def register_commands(subparsers, config) -> None:
    """Register all public CLI commands."""
    register_start(subparsers, config)
    register_stop(subparsers, config)
    register_status(subparsers, config)
    register_chat(subparsers, config)
    register_session(subparsers, config)
    register_trace(subparsers, config)
    register_tui(subparsers, config)


__all__ = ["register_commands"]
