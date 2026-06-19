"""Core composition root and daemon lifecycle."""

import asyncio
import os

from src.core.agent.contracts import ManagedAgentService
from src.core.bus.router import RpcRouter
from src.core.container import CoreContainer
from src.core.container_factories import (
    TransportFactory,
    create_socket_transport,
)
from src.core.config.models import CoreConfig
from src.core.handlers import AgentHandlers, CoreHandlers
from src.core.telemetry import EventBus, install_event_bus
from src.ipc.auth import ensure_runtime_dir, pid_path
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
        container: CoreContainer | None = None,
    ) -> None:
        self.config = config
        self.shutdown_event = asyncio.Event()
        self._pool = None
        self._state_database = None
        self._event_bus = event_bus
        self._trace_recorder = trace_recorder
        self.container = container
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
            self.container = self.container or CoreContainer()
            self.container.config.override(config)
            self.container.auth_token.override(auth_token)
            self.container.shutdown_event.override(self.shutdown_event)
            self.container.trace_recorder.override(self._trace_recorder)
            self.container.transport_factory.override(transport_factory)
            if event_bus is not None:
                self.container.event_bus.override(event_bus)
            self._state_database = self.container.state_database()
            self._pool = self.container.postgres_pool()
            self._event_bus = self.container.event_bus()
            self.agent_service = self.container.agent_service()
            self.router = self.container.router()
            self.core_handlers = self.container.core_handlers()
            self.agent_handlers = self.container.agent_handlers()
            self.core_handlers.register(self.router)
            self.agent_handlers.register(self.router)
            self.transport = self.container.transport()
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
