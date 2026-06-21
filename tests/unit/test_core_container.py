import asyncio
import unittest

from dependency_injector import providers

from src.core.adapters.sqlite import SQLiteSummaryStore
from src.core.agent.service import AgentTurnService
from src.core.agent.loop import TurnExecutionLoop
from src.core.agent.request_stream import AgentRequestStreamService
from src.core.agent.runtime_graph import RuntimeGraphResolver
from src.core.agent.slices import SliceExecutionService
from src.core.agent.worker import TurnWorkerExecutor
from src.core.config.models import CoreConfig
from src.core.container import CoreContainer
from src.core.diagnostics import DiagnosticTurnService
from src.core.execution import ExecutionLifecycleService
from src.core.session import SessionLifecycleService
from src.core.transport.socket_server import SocketServer


class CoreContainerTest(unittest.TestCase):
    def _container(self) -> CoreContainer:
        container = CoreContainer()
        container.config.override(CoreConfig.load(port=0, manage_runtime_files=False))
        container.auth_token.override("token")
        container.shutdown_event.override(asyncio.Event())
        self.addCleanup(container.unwire)
        return container

    def test_constructs_agent_service_and_transport(self):
        container = self._container()

        agent_service = container.agent_service()
        session_service = container.session_lifecycle_service()
        execution_service = container.execution_lifecycle_service()

        self.assertIsInstance(agent_service, AgentTurnService)
        self.assertIsInstance(session_service, SessionLifecycleService)
        self.assertIsInstance(execution_service, ExecutionLifecycleService)

        stream_service = agent_service.request_stream_service
        execution_loop = stream_service.turn_execution_loop
        self.assertIsInstance(stream_service, AgentRequestStreamService)
        self.assertIsInstance(stream_service.runtime_graph_resolver, RuntimeGraphResolver)
        self.assertIsInstance(
            agent_service.async_turn_runner.turn_worker,
            TurnWorkerExecutor,
        )
        self.assertIs(
            stream_service.execution_lifecycle.execution_store,
            execution_service.execution_store,
        )
        self.assertIs(stream_service.lock_registry, session_service.lock_registry)
        self.assertIsInstance(
            stream_service.diagnostic_turn_service,
            DiagnosticTurnService,
        )
        self.assertIsInstance(execution_loop, TurnExecutionLoop)
        self.assertIsInstance(
            execution_loop.slice_execution_service,
            SliceExecutionService,
        )
        self.assertIs(execution_loop.observer, execution_loop.error_handler.observer)
        self.assertIs(execution_loop.observer, execution_loop.pause_handler.observer)
        self.assertIsInstance(container.transport(), SocketServer)

    def test_maintenance_store_adapters_are_short_lived(self):
        container = self._container()

        first = container.summary_store()
        second = container.summary_store()

        self.assertIsInstance(first, SQLiteSummaryStore)
        self.assertIsInstance(second, SQLiteSummaryStore)
        self.assertIsNot(first, second)
        self.assertIs(first.database, second.database)

    def test_process_level_state_database_is_singleton(self):
        container = self._container()

        self.assertIs(container.state_database(), container.state_database())
        self.assertIs(
            container.workspace_repository().database,
            container.state_database(),
        )

    def test_overrides_transport_factory(self):
        container = self._container()

        class FakeTransport:
            pass

        def fake_transport(_config, _router):
            return FakeTransport()

        container.transport_factory.override(fake_transport)

        self.assertIsInstance(container.transport(), FakeTransport)

    def test_overrides_agent_service(self):
        container = self._container()
        fake_service = object()
        container.agent_service.override(providers.Object(fake_service))

        self.assertIs(fake_service, container.agent_service())


if __name__ == "__main__":
    unittest.main()
