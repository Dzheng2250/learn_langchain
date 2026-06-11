import asyncio
import time
import unittest
from unittest.mock import call, patch

from src.core.app import CoreApp
from src.core.config.models import CoreConfig


class FakeAgentService:
    def __init__(self, events, fail_initialize=False):
        self.events = events
        self.fail_initialize = fail_initialize

    def initialize(self):
        self.events.append("agent.initialize")
        if self.fail_initialize:
            raise RuntimeError("initialize failed")

    def close(self):
        self.events.append("agent.close")

    async def run_turn(self, *_args, **_kwargs):
        return {"status": "ok"}


class FakeTransport:
    def __init__(self, events, fail_start=False):
        self.events = events
        self.fail_start = fail_start

    async def start(self):
        self.events.append("transport.start")
        if self.fail_start:
            raise RuntimeError("transport start failed")
        return 0

    async def close(self, _timeout):
        self.events.append("transport.close")


class FakePool:
    def __init__(self):
        self.close_timeouts = []

    def close(self, *, timeout):
        self.close_timeouts.append(timeout)


class CoreAppTest(unittest.IsolatedAsyncioTestCase):
    def _config(self):
        return CoreConfig.load(port=0, manage_runtime_files=False)

    async def test_lifecycle_initializes_and_closes_in_reverse_order(self):
        events = []
        app = CoreApp(
            self._config(),
            "token",
            agent_service=FakeAgentService(events),
            transport_factory=lambda _config, _router: FakeTransport(events),
        )
        await app.start()
        await app.close()
        self.assertEqual(
            ["agent.initialize", "transport.start", "transport.close", "agent.close"],
            events,
        )

    async def test_start_failure_closes_created_resources(self):
        events = []
        app = CoreApp(
            self._config(),
            "token",
            agent_service=FakeAgentService(events, fail_initialize=True),
            transport_factory=lambda _config, _router: FakeTransport(events),
        )
        with self.assertRaises(RuntimeError):
            await app.start()
        self.assertEqual(["agent.initialize", "transport.close", "agent.close"], events)

    async def test_transport_start_failure_closes_created_resources(self):
        events = []
        app = CoreApp(
            self._config(),
            "token",
            agent_service=FakeAgentService(events),
            transport_factory=lambda _config, _router: FakeTransport(events, fail_start=True),
        )
        with self.assertRaises(RuntimeError):
            await app.start()
        self.assertEqual(
            ["agent.initialize", "transport.start", "transport.close", "agent.close"],
            events,
        )

    async def test_close_is_idempotent(self):
        events = []
        app = CoreApp(
            self._config(),
            "token",
            agent_service=FakeAgentService(events),
            transport_factory=lambda _config, _router: FakeTransport(events),
        )
        await app.start()
        await app.close()
        await app.close()
        self.assertEqual(1, events.count("transport.close"))
        self.assertEqual(1, events.count("agent.close"))

    async def test_event_publisher_is_installed_only_during_app_lifecycle(self):
        events = []
        publisher = object()
        with patch("src.core.app.set_event_publisher") as set_publisher:
            app = CoreApp(
                self._config(),
                "token",
                agent_service=FakeAgentService(events),
                transport_factory=lambda _config, _router: FakeTransport(events),
                event_publisher=publisher,
            )
            set_publisher.assert_not_called()

            await app.start()
            set_publisher.assert_called_once_with(publisher)

            await app.close()
            self.assertEqual(
                [call(publisher), call(None)],
                set_publisher.call_args_list,
            )

    async def test_close_releases_lazily_created_default_event_publisher(self):
        events = []
        with patch("src.core.app.set_event_publisher") as set_publisher:
            app = CoreApp(
                self._config(),
                "token",
                agent_service=FakeAgentService(events),
                transport_factory=lambda _config, _router: FakeTransport(events),
            )
            await app.start()
            await app.close()

            set_publisher.assert_called_once_with(None)

    async def test_close_passes_shutdown_timeout_to_pool(self):
        events = []
        app = CoreApp(
            self._config(),
            "token",
            agent_service=FakeAgentService(events),
            transport_factory=lambda _config, _router: FakeTransport(events),
        )
        pool = FakePool()
        app._pool = pool

        await app.close()

        self.assertEqual([app.config.shutdown_timeout_seconds], pool.close_timeouts)

    async def test_blocking_service_close_does_not_block_event_loop(self):
        events = []

        class SlowCloseService(FakeAgentService):
            def close(self):
                time.sleep(0.05)
                super().close()

        app = CoreApp(
            self._config(),
            "token",
            agent_service=SlowCloseService(events),
            transport_factory=lambda _config, _router: FakeTransport(events),
        )

        close_task = asyncio.create_task(app.close())
        await asyncio.sleep(0.01)
        self.assertFalse(close_task.done())
        await close_task

    async def test_close_releases_remaining_resources_when_agent_close_fails(self):
        events = []
        pool = FakePool()

        class FailingCloseService(FakeAgentService):
            def close(self):
                super().close()
                raise RuntimeError("agent close failed")

        with patch("src.core.app.set_event_publisher") as set_publisher:
            app = CoreApp(
                self._config(),
                "token",
                agent_service=FailingCloseService(events),
                transport_factory=lambda _config, _router: FakeTransport(events),
            )
            app._pool = pool

            with self.assertRaisesRegex(RuntimeError, "agent close failed"):
                await app.close()

            set_publisher.assert_called_once_with(None)
            self.assertEqual([app.config.shutdown_timeout_seconds], pool.close_timeouts)

    def test_core_config_rejects_non_loopback_host(self):
        with self.assertRaises(ValueError):
            CoreConfig.load(host="0.0.0.0", manage_runtime_files=False)


if __name__ == "__main__":
    unittest.main()
