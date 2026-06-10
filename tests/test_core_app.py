import asyncio
import unittest

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

    def run_turn(self, *_args, **_kwargs):
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

    def test_core_config_rejects_non_loopback_host(self):
        with self.assertRaises(ValueError):
            CoreConfig.load(host="0.0.0.0", manage_runtime_files=False)


if __name__ == "__main__":
    unittest.main()
