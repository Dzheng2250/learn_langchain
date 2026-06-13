"""Core composition root and daemon lifecycle."""

import asyncio
import os
from collections.abc import Callable
from typing import Protocol

from src.core.agent.contracts import ManagedAgentService
from src.core.agent.service import AgentTurnService
from src.core.agent.models import RunLimits
from src.core.bus.router import RpcRouter
from src.core.config.models import CoreConfig
from src.core.handlers import AgentHandlers, CoreHandlers
from src.core.telemetry import EventBus, create_event_bus, install_event_bus
from src.core.transport.socket_server import SocketServer
from src.ipc.auth import ensure_runtime_dir, pid_path
from src.core.database.connection import create_pool
from src.core.state import (
    CheckpointManager,
    ExecutionRepository,
    LocalStateDatabase,
    LocalStateStore,
    LocalWorkspaceRepository,
)
from src.core.context.manager import AgentContextManager
from src.core.llm.provider import OpenAICompatibleProvider
from src.core.workspace.runtime import WorkspaceRuntimeFactory, WorkspaceRuntimeRegistry


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


class CoreApp:
    """Compose Core services and own their startup and shutdown order."""

    def __init__(
        self,
        config: CoreConfig,
        auth_token: str,
        *,
        agent_service: ManagedAgentService | None = None,
        transport_factory: TransportFactory = create_socket_transport,
        event_bus: EventBus | None = None,
    ) -> None:
        self.config = config
        self.shutdown_event = asyncio.Event()
        self._pool = None
        self._state_database = None
        self._event_bus = event_bus
        if agent_service is None:
            # CoreApp is the process-level composition root. These objects are
            # intentionally shared across requests; workspace-specific graphs
            # and tools are created lazily by WorkspaceRuntimeRegistry.
            self._state_database = LocalStateDatabase()
            checkpoint_manager = CheckpointManager()
            # PostgreSQL is optional. It is opened only for explicitly enabled
            # telemetry/projection features, never for authoritative Session IO.
            from src.config.settings import AGENT_EVENTS_POSTGRES_ENABLED

            self._pool = create_pool() if AGENT_EVENTS_POSTGRES_ENABLED else None
            if self._event_bus is None:
                self._event_bus = create_event_bus(self._pool)
            model_provider = OpenAICompatibleProvider()
            run_limits = RunLimits()
            self.agent_service = AgentTurnService(
                workspace_repository=LocalWorkspaceRepository(self._state_database),
                runtime_registry=WorkspaceRuntimeRegistry(
                    factory=WorkspaceRuntimeFactory(
                        model_provider,
                        run_limits,
                        checkpointer=checkpoint_manager.initialize(),
                    ),
                ),
                memory_store_factory=lambda: LocalStateStore(
                    self._state_database,
                    model_provider=model_provider,
                ),
                context_manager=AgentContextManager(model_provider=model_provider),
                model_configuration=model_provider,
                run_limits=run_limits,
                execution_repository=ExecutionRepository(self._state_database),
                checkpoint_manager=checkpoint_manager,
            )
        else:
            self.agent_service = agent_service
        self.router = RpcRouter(auth_token)
        self.core_handlers = CoreHandlers(self.shutdown_event)
        self.agent_handlers = AgentHandlers(self.agent_service)
        self.core_handlers.register(self.router)
        self.agent_handlers.register(self.router)
        # Transport depends only on the validated router. It never imports or
        # invokes Agent internals directly.
        self.transport = transport_factory(config, self.router)
        self._started = False
        self._closed = False

    async def start(self) -> None:
        """Initialize services, start transport, then publish the PID."""
        if self._started:
            return
        try:
            install_event_bus(self._event_bus)
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
            try:
                await asyncio.to_thread(self.agent_service.close)
            finally:
                try:
                    await asyncio.to_thread(install_event_bus, None)
                    if self._pool is not None:
                        await asyncio.to_thread(
                            self._pool.close,
                            timeout=self.config.shutdown_timeout_seconds,
                        )
                finally:
                    if self._state_database is not None:
                        self._state_database.close()
                    if self.config.manage_runtime_files:
                        try:
                            pid_path(self.config.runtime_dir).unlink(missing_ok=True)
                        except OSError:
                            pass
