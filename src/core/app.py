"""Core composition root and daemon lifecycle."""

import asyncio
import os
from collections.abc import Callable
from typing import Protocol

from src.core.agent.service import AgentTurnService
from src.core.bus.router import RpcRouter
from src.core.config.models import CoreConfig
from src.core.handlers import AgentHandlers, CoreHandlers
from src.core.handlers.agent import AgentTurnRunner
from src.core.hooks.events import set_event_sinks
from src.core.transport.socket_server import SocketServer
from src.ipc.auth import ensure_runtime_dir, pid_path
from src.core.database.connection import create_pool
from src.core.memory.store import PostgresMemoryStore
from src.core.workspace.repository import WorkspaceRepository
from src.core.workspace.runtime import WorkspaceRuntimeRegistry


class CoreAgentService(AgentTurnRunner, Protocol):
    def initialize(self) -> None:
        """Initialize durable dependencies."""

    def close(self) -> None:
        """Close service-owned resources."""


class CoreTransport(Protocol):
    async def start(self) -> int:
        """Start accepting requests and return the bound port."""

    async def close(self, timeout_seconds: float) -> None:
        """Stop accepting requests and close transport resources."""


TransportFactory = Callable[[CoreConfig, RpcRouter], CoreTransport]


def create_socket_transport(config: CoreConfig, router: RpcRouter) -> SocketServer:
    return SocketServer(
        config.host,
        config.port,
        router,
        max_message_bytes=config.max_message_bytes,
    )


class CoreApp:
    """Compose Core services and own their startup and shutdown order."""

    def __init__(
        self,
        config: CoreConfig,
        auth_token: str,
        *,
        agent_service: CoreAgentService | None = None,
        transport_factory: TransportFactory = create_socket_transport,
    ) -> None:
        self.config = config
        self.shutdown_event = asyncio.Event()
        self._pool = None
        if agent_service is None:
            self._pool = create_pool()
            self.agent_service = AgentTurnService(
                workspace_repository=WorkspaceRepository(self._pool),
                runtime_registry=WorkspaceRuntimeRegistry(),
                memory_store_factory=lambda: PostgresMemoryStore(pool=self._pool),
            )
        else:
            self.agent_service = agent_service
        self.router = RpcRouter(auth_token)
        self.core_handlers = CoreHandlers(self.shutdown_event)
        self.agent_handlers = AgentHandlers(self.agent_service)
        self.core_handlers.register(self.router)
        self.agent_handlers.register(self.router)
        self.transport = transport_factory(config, self.router)
        self._started = False
        self._closed = False

    async def start(self) -> None:
        """Initialize services, start transport, then publish the PID."""
        if self._started:
            return
        try:
            self.agent_service.initialize()
            if self.config.manage_runtime_files:
                ensure_runtime_dir(self.config.runtime_dir)
            await self.transport.start()
            if self.config.manage_runtime_files:
                pid_path(self.config.runtime_dir).write_text(str(os.getpid()), encoding="ascii")
            self._started = True
        except Exception:
            await self.close()
            raise

    async def run(self) -> None:
        """Run until a registered lifecycle handler requests shutdown."""
        await self.start()
        try:
            await self.shutdown_event.wait()
        finally:
            await self.close()

    async def close(self) -> None:
        """Close resources in reverse creation order."""
        if self._closed:
            return
        self._closed = True
        try:
            await self.transport.close(self.config.shutdown_timeout_seconds)
        finally:
            self.agent_service.close()
            set_event_sinks(None)
            if self._pool is not None:
                self._pool.close()
            if self.config.manage_runtime_files:
                try:
                    pid_path(self.config.runtime_dir).unlink(missing_ok=True)
                except OSError:
                    pass
