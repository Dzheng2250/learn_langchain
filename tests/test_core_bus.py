import asyncio
import json
import unittest
from unittest.mock import patch

from src.cli.client import CoreClient
from src.core.app import CoreApp
from src.core.config.models import CoreConfig
from src.core.transport.framing import FrameError, encode_ndjson, read_ndjson
from src.ipc.models import PingParams
from src.core.bus.router import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    UNAUTHORIZED,
    RpcRouter,
)
from src.core.transport.socket_server import SocketRequestContext


TOKEN = "test-token"


class FakeRequestContext:
    request_id = "request"

    async def send_notification(self, _value):
        pass

    def request_close(self):
        pass


class FakeAgentService:
    def initialize(self):
        pass

    def close(self):
        pass

    def run_turn(self, session_id, message, on_event, *, run_id=None):
        on_event({"event": "token", "data": {"content": "hello"}})
        on_event({"event": "done", "data": {"status": "ok"}})
        return {"status": "ok", "run_id": run_id}


class RouterTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = 0
        self.router = RpcRouter(TOKEN)
        self.context = FakeRequestContext()

        async def handler(_params, _context):
            self.calls += 1
            return {"status": "ok"}

        self.router.register("core.ping", PingParams, handler)

    async def test_valid_request_reaches_handler(self):
        response = await self.router.dispatch(
            {"jsonrpc": "2.0", "id": "1", "method": "core.ping", "params": {"auth_token": TOKEN}},
            self.context,
        )
        self.assertEqual({"status": "ok"}, response.result)
        self.assertEqual(1, self.calls)

    async def test_bad_token_never_reaches_handler(self):
        response = await self.router.dispatch(
            {"jsonrpc": "2.0", "id": "1", "method": "core.ping", "params": {"auth_token": "bad"}},
            self.context,
        )
        self.assertEqual(UNAUTHORIZED, response.error.code)
        self.assertEqual(0, self.calls)

    async def test_unknown_method_and_invalid_params(self):
        missing = await self.router.dispatch(
            {"jsonrpc": "2.0", "id": "1", "method": "missing", "params": {"auth_token": TOKEN}},
            self.context,
        )
        invalid = await self.router.dispatch(
            {"jsonrpc": "2.0", "id": "2", "method": "core.ping", "params": {}},
            self.context,
        )
        self.assertEqual(METHOD_NOT_FOUND, missing.error.code)
        self.assertEqual(INVALID_PARAMS, invalid.error.code)

    async def test_duplicate_method_registration_is_rejected(self):
        async def handler(_params, _context):
            return {}

        with self.assertRaises(ValueError):
            self.router.register("core.ping", PingParams, handler)


class FramingTest(unittest.IsolatedAsyncioTestCase):
    async def test_reads_ndjson_object(self):
        reader = asyncio.StreamReader()
        reader.feed_data(encode_ndjson({"a": 1}))
        reader.feed_eof()
        self.assertEqual({"a": 1}, await read_ndjson(reader, 100))

    async def test_rejects_invalid_json_and_large_message(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b"not-json\n")
        reader.feed_eof()
        with self.assertRaises(FrameError):
            await read_ndjson(reader, 100)

        reader = asyncio.StreamReader()
        reader.feed_data(b'{"value":"too long"}\n')
        reader.feed_eof()
        with self.assertRaises(FrameError):
            await read_ndjson(reader, 5)


class YieldingWriter:
    def __init__(self):
        self.frames = []
        self.pending = b""

    def write(self, data):
        self.pending += data

    async def drain(self):
        await asyncio.sleep(0)
        self.frames.append(self.pending)
        self.pending = b""


class ConnectionWriteTest(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_writes_remain_complete_ndjson_frames(self):
        writer = YieldingWriter()
        context = SocketRequestContext(writer)
        await asyncio.gather(
            context.send_notification({"id": "a", "value": "first"}),
            context.send_notification({"id": "b", "value": "second"}),
        )
        self.assertEqual(2, len(writer.frames))
        for frame in writer.frames:
            self.assertTrue(frame.endswith(b"\n"))
            json.loads(frame.decode("utf-8"))


class CoreServerIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        config = CoreConfig.load(
            port=0,
            manage_runtime_files=False,
        )
        self.app = CoreApp(
            config,
            TOKEN,
            agent_service=FakeAgentService(),
        )
        await self.app.start()

    async def asyncTearDown(self):
        await self.app.close()

    async def _request(self, method, params):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.app.transport.port)
        request_id = "request-1"
        writer.write(
            encode_ndjson(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            )
        )
        await writer.drain()
        messages = []
        while True:
            raw = json.loads((await reader.readline()).decode("utf-8"))
            messages.append(raw)
            if raw.get("id") == request_id:
                break
        writer.close()
        await writer.wait_closed()
        return messages

    async def test_ping(self):
        messages = await self._request("core.ping", {"auth_token": TOKEN})
        self.assertEqual("ok", messages[-1]["result"]["status"])

    async def test_cli_client_can_ping_server(self):
        client = CoreClient(port=self.app.transport.port)
        with patch("src.cli.client.read_token", return_value=TOKEN):
            result = await asyncio.to_thread(client.request, "core.ping")
        self.assertEqual("ok", result["status"])

    async def test_chat_streams_notifications_then_result(self):
        messages = await self._request(
            "agent.chat",
            {"auth_token": TOKEN, "session_id": "s", "message": "hello"},
        )
        notifications = [message for message in messages if message.get("method") == "agent.event"]
        self.assertEqual(["token", "done"], [item["params"]["event"] for item in notifications])
        self.assertTrue(all(item["params"]["request_id"] == "request-1" for item in notifications))
        self.assertEqual("ok", messages[-1]["result"]["status"])

    async def test_shutdown_returns_response_and_sets_shutdown_event(self):
        messages = await self._request("core.shutdown", {"auth_token": TOKEN})
        self.assertEqual("shutting_down", messages[-1]["result"]["status"])
        await asyncio.wait_for(self.app.shutdown_event.wait(), timeout=1)


if __name__ == "__main__":
    unittest.main()
