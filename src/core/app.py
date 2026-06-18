"""Core composition root and daemon lifecycle."""

import asyncio
import os
from collections.abc import Callable
from typing import Protocol

from src.core.agent.contracts import ManagedAgentService
from src.core.agent.service import AgentTurnService
from src.core.agent.models import RunLimits
from src.config.maintenance import MaintenanceSettings
from src.core.adapters.sqlite import SQLiteStateUnitOfWorkFactory
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
from src.core.finalization import CompletedTurnCommitter, TurnFinalizer
from src.core.maintenance import (
    ExecutionRecoveryCoordinator,
    MaintenanceJobType,
    MaintenanceRepository,
    MaintenanceScheduler,
)
from src.core.maintenance.handlers import (
    CheckpointCleanupHandler,
    ContextSummaryHandler,
    MemoryExtractionHandler,
)
from src.core.context.manager import AgentContextManager
from src.core.errors import ProviderErrorHandler
from src.core.llm.provider import OpenAICompatibleProvider
from src.core.tasks import TaskPlanningService, TaskRepository
from src.core.workspace.runtime import WorkspaceRuntimeFactory, WorkspaceRuntimeRegistry
from src.config.paths import trace_dir
from src.core.tracing import (
    TraceDirection,
    TraceLayer,
    TraceRecorder,
    TraceWriter,
    TracingModelProvider,
    install_trace_recorder,
    record_trace,
)


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
        trace_recorder=None,
    ) -> None:
        self.config = config
        self.shutdown_event = asyncio.Event()
        self._pool = None
        self._state_database = None
        self._event_bus = event_bus
        self._trace_recorder = trace_recorder
        if self._trace_recorder is None:
            from src.config.settings import (
                TRACE_BATCH_SIZE,
                TRACE_ENABLED,
                TRACE_FLUSH_INTERVAL_SECONDS,
                TRACE_QUEUE_MAX_SIZE,
                TRACE_RETENTION_DAYS,
            )

            if TRACE_ENABLED and self.config.manage_runtime_files:
                writer = TraceWriter(
                    trace_dir(),
                    retention_days=TRACE_RETENTION_DAYS,
                    batch_size=TRACE_BATCH_SIZE,
                    flush_interval_seconds=TRACE_FLUSH_INTERVAL_SECONDS,
                    queue_max_size=TRACE_QUEUE_MAX_SIZE,
                )
                writer.cleanup()
                self._trace_recorder = TraceRecorder(writer)
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
                self._event_bus = create_event_bus(
                    self._pool,
                    include_trace_sink=self._trace_recorder is not None,
                )
            base_model_provider = OpenAICompatibleProvider()
            model_provider = TracingModelProvider(base_model_provider)
            run_limits = RunLimits()
            workspace_repository = LocalWorkspaceRepository(self._state_database)
            execution_repository = ExecutionRepository(self._state_database)
            maintenance_settings = MaintenanceSettings.load()
            maintenance_repository = MaintenanceRepository(
                self._state_database,
                maintenance_settings,
            )
            task_repository = TaskRepository(self._state_database)
            task_service = TaskPlanningService(task_repository)
            context_manager = AgentContextManager(model_provider=model_provider)

            def state_store_factory():
                return LocalStateStore(
                    self._state_database,
                    model_provider=model_provider,
                )

            maintenance_scheduler = MaintenanceScheduler(
                maintenance_repository,
                {
                    MaintenanceJobType.CONTEXT_SUMMARY: ContextSummaryHandler(
                        workspace_repository,
                        state_store_factory,
                        context_manager,
                    ),
                    MaintenanceJobType.MEMORY_EXTRACT: MemoryExtractionHandler(
                        workspace_repository,
                        state_store_factory,
                    ),
                    MaintenanceJobType.CHECKPOINT_CLEANUP: CheckpointCleanupHandler(
                        checkpoint_manager,
                        execution_repository,
                    ),
                },
                settings=maintenance_settings,
            )
            turn_finalizer = TurnFinalizer(
                context_manager,
                CompletedTurnCommitter(
                    SQLiteStateUnitOfWorkFactory(
                        self._state_database,
                        execution_repository,
                        maintenance_repository,
                    ),
                ),
                maintenance_scheduler,
            )
            self.agent_service = AgentTurnService(
                workspace_repository=workspace_repository,
                runtime_registry=WorkspaceRuntimeRegistry(
                    factory=WorkspaceRuntimeFactory(
                        model_provider,
                        run_limits,
                        checkpointer_provider=checkpoint_manager.initialize,
                        task_service=task_service,
                    ),
                ),
                state_store_factory=state_store_factory,
                context_manager=context_manager,
                model_configuration=model_provider,
                run_limits=run_limits,
                execution_repository=execution_repository,
                checkpoint_manager=checkpoint_manager,
                turn_finalizer=turn_finalizer,
                maintenance_repository=maintenance_repository,
                maintenance_scheduler=maintenance_scheduler,
                recovery_coordinator=ExecutionRecoveryCoordinator(
                    execution_repository,
                    checkpoint_manager,
                    maintenance_repository,
                ),
                provider_error_handler=ProviderErrorHandler(),
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
            install_trace_recorder(self._trace_recorder)
            record_trace(
                TraceDirection.INTERNAL,
                TraceLayer.LIFECYCLE,
                "core.starting",
            )
            install_event_bus(self._event_bus)
            self.agent_service.initialize()
            if self.config.manage_runtime_files:
                ensure_runtime_dir(self.config.runtime_dir)
            bound_port = await self.transport.start()
            if self.config.manage_runtime_files:
                pid_path(self.config.runtime_dir).write_text(str(os.getpid()), encoding="ascii")
            self._started = True
            record_trace(
                TraceDirection.INTERNAL,
                TraceLayer.LIFECYCLE,
                "core.started",
                data={"host": self.config.host, "port": bound_port},
            )
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
        record_trace(
            TraceDirection.INTERNAL,
            TraceLayer.LIFECYCLE,
            "core.stopping",
        )
        try:
            await self.transport.close(self.config.shutdown_timeout_seconds)
        finally:
            try:
                await asyncio.to_thread(self.agent_service.close)
            finally:
                try:
                    try:
                        await asyncio.to_thread(install_event_bus, None)
                        record_trace(
                            TraceDirection.INTERNAL,
                            TraceLayer.LIFECYCLE,
                            "core.stopped",
                        )
                    finally:
                        try:
                            if self._trace_recorder is not None:
                                await asyncio.to_thread(
                                    self._trace_recorder.close,
                                    self.config.shutdown_timeout_seconds,
                                )
                        finally:
                            install_trace_recorder(None)
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
