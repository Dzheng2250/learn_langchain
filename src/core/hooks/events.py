"""Deprecated compatibility imports for :mod:`src.core.telemetry`."""

from src.core.telemetry import *
from src.core.hooks.models import AgentEvent, AgentEventContext

set_event_publisher = install_event_bus
flush_event_sinks = flush_events
set_event_context = bind_context
set_run_event_context = bind_run_context
get_event_context = current_context
reset_event_context = reset_context


def set_event_sinks(sinks) -> None:
    """Install a temporary sink-backed bus for legacy tests and integrations."""
    install_event_bus(EventBus(list(sinks)) if sinks is not None else None)
