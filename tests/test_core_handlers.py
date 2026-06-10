import asyncio
import unittest

from src.core.handlers.agent import AgentHandlers
from src.core.handlers.core import CoreHandlers
from src.ipc.models import ChatParams, PingParams, ShutdownParams


class FakeRequestContext:
    def __init__(self):
        self.request_id = "request-1"
        self.notifications = []
        self.close_requested = False

    async def send_notification(self, value):
        self.notifications.append(value)

    def request_close(self):
        self.close_requested = True


class FakeAgentService:
    def run_turn(self, session_id, message, on_event, *, run_id=None):
        on_event({"event": "token", "data": {"content": "hello"}})
        return {"status": "ok", "run_id": run_id, "session_id": session_id, "message": message}


class CoreHandlersTest(unittest.IsolatedAsyncioTestCase):
    async def test_ping_returns_health_data(self):
        handlers = CoreHandlers(asyncio.Event(), server_version="test")
        result = await handlers.ping(PingParams(auth_token="token"), FakeRequestContext())
        self.assertEqual("ok", result["status"])
        self.assertEqual("test", result["server_version"])

    async def test_shutdown_requests_connection_close_and_app_shutdown(self):
        shutdown_event = asyncio.Event()
        handlers = CoreHandlers(shutdown_event)
        context = FakeRequestContext()
        result = await handlers.shutdown(ShutdownParams(auth_token="token"), context)
        await asyncio.sleep(0)
        self.assertTrue(context.close_requested)
        self.assertTrue(shutdown_event.is_set())
        self.assertEqual("shutting_down", result["status"])


class AgentHandlersTest(unittest.IsolatedAsyncioTestCase):
    async def test_chat_adapts_service_events_to_notifications(self):
        handlers = AgentHandlers(FakeAgentService())
        context = FakeRequestContext()
        result = await handlers.chat(
            ChatParams(auth_token="token", session_id="session", message="hello"),
            context,
        )
        self.assertEqual("ok", result["status"])
        self.assertEqual(1, len(context.notifications))
        params = context.notifications[0].params
        self.assertEqual("request-1", params["request_id"])
        self.assertEqual("token", params["event"])


if __name__ == "__main__":
    unittest.main()
