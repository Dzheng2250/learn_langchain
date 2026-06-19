"""Factory helpers used by the Core dependency-injector container."""

from collections.abc import Callable
from typing import Protocol

from src.core.bus.router import RpcRouter
from src.core.config.models import CoreConfig
from src.core.database.connection import create_pool
from src.core.maintenance import MaintenanceJobType
from src.core.telemetry import create_event_bus
from src.core.transport.socket_server import SocketServer


class CoreTransport(Protocol):
    """Transport lifecycle required by the Core composition root."""

    async def start(self) -> int:
        """Start accepting requests and return the bound port."""

    async def close(self, timeout_seconds: float) -> None:
        """Stop accepting requests and close transport resources."""


TransportFactory = Callable[[CoreConfig, RpcRouter], CoreTransport]


def create_socket_transport(config: CoreConfig, router: RpcRouter) -> SocketServer:
    """Create the default TCP/NDJSON transport from validated Core config."""
    return SocketServer(
        config.host,
        config.port,
        router,
        max_message_bytes=config.max_message_bytes,
    )


def create_optional_postgres_pool():
    """Open PostgreSQL only when optional event/projection features need it."""
    from src.config.settings import AGENT_EVENTS_POSTGRES_ENABLED

    return create_pool() if AGENT_EVENTS_POSTGRES_ENABLED else None


def create_default_event_bus(pool, trace_recorder):
    """Build the default event bus with optional trace mirroring."""
    return create_event_bus(
        pool,
        include_trace_sink=trace_recorder is not None,
    )


def create_transport(
    config: CoreConfig,
    router: RpcRouter,
    transport_factory: TransportFactory,
) -> CoreTransport:
    """Invoke the configured transport factory from the DI container."""
    return transport_factory(config, router)


def maintenance_handlers(
    context_summary_handler,
    memory_extraction_handler,
    checkpoint_cleanup_handler,
) -> dict:
    """Return the scheduler handler map keyed by stable maintenance enum values."""
    return {
        MaintenanceJobType.CONTEXT_SUMMARY: context_summary_handler,
        MaintenanceJobType.MEMORY_EXTRACT: memory_extraction_handler,
        MaintenanceJobType.CHECKPOINT_CLEANUP: checkpoint_cleanup_handler,
    }
