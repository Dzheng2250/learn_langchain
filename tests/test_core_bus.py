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
from src.core.tracing import TraceRecorder


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

    async def run_turn(
        self,
        workspace_root,
        session_name,
        message,
        on_event,
        *,
        run_id=None,
        control=None,
    ):
        await asyncio.to_thread(on_event, {"event": "token", "data": {"content": "hello"}})
        await asyncio.to_thread(on_event, {"event": "done", "data": {"status": "ok"}})
        return {"status": "ok", "run_id": run_id}


class MemoryTraceWriter:
    def __init__(self):
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def flush(self):
        pass

    def close(self, _timeout=2):
        pass


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
        self.trace_writer = MemoryTraceWriter()
        self.app = CoreApp(
            config,
            TOKEN,
            agent_service=FakeAgentService(),
            trace_recorder=TraceRecorder(self.trace_writer, daemon_id="test-daemon"),
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
            {"auth_token": TOKEN, "workspace_root": ".", "session_name": "s", "message": "hello"},
        )
        notifications = [message for message in messages if message.get("method") == "agent.event"]
        self.assertEqual(["token", "done"], [item["params"]["event"] for item in notifications])
        self.assertTrue(all(item["params"]["request_id"] == "request-1" for item in notifications))
        self.assertEqual("ok", messages[-1]["result"]["status"])

        relevant = [
            record
            for record in self.trace_writer.records
            if record.request_id == "request-1"
        ]
        kinds = {record.kind for record in relevant}
        self.assertTrue(
            {
                "ipc.request_received",
                "ipc.request_validated",
                "ipc.notification_sent",
                "ipc.response_sent",
            }.issubset(kinds)
        )
        self.assertEqual(1, len({record.trace_id for record in relevant}))
        self.assertEqual(1, len({record.run_id for record in relevant if record.run_id}))
        request_received = next(
            record for record in relevant if record.kind == "ipc.request_received"
        )
        self.assertGreater(request_received.data["bytes"], 0)

    async def test_shutdown_returns_response_and_sets_shutdown_event(self):
        messages = await self._request("core.shutdown", {"auth_token": TOKEN})
        self.assertEqual("shutting_down", messages[-1]["result"]["status"])
        await asyncio.wait_for(self.app.shutdown_event.wait(), timeout=1)

    async def test_malformed_frame_only_closes_its_own_connection(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.app.transport.port)
        writer.write(b"not-json\n")
        await writer.drain()
        response = json.loads((await reader.readline()).decode("utf-8"))
        self.assertEqual(-32700, response["error"]["code"])
        self.assertEqual(b"", await reader.readline())
        writer.close()
        await writer.wait_closed()

        messages = await self._request("core.ping", {"auth_token": TOKEN})
        self.assertEqual("ok", messages[-1]["result"]["status"])

    async def test_concurrent_connections_receive_independent_responses(self):
        first, second = await asyncio.gather(
            self._request("core.ping", {"auth_token": TOKEN}),
            self._request("core.ping", {"auth_token": TOKEN}),
        )
        self.assertEqual("ok", first[-1]["result"]["status"])
        self.assertEqual("ok", second[-1]["result"]["status"])

    async def test_graceful_close_waits_for_active_request(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_handler(_params, _context):
            started.set()
            await release.wait()
            return {"status": "ok"}

        self.app.router.register("test.slow", PingParams, slow_handler)
        reader, writer = await asyncio.open_connection("127.0.0.1", self.app.transport.port)
        writer.write(
            encode_ndjson(
                {
                    "jsonrpc": "2.0",
                    "id": "slow",
                    "method": "test.slow",
                    "params": {"auth_token": TOKEN},
                }
            )
        )
        await writer.drain()
        await started.wait()

        close_task = asyncio.create_task(self.app.close())
        await asyncio.sleep(0.01)
        self.assertFalse(close_task.done())
        release.set()
        await asyncio.wait_for(close_task, timeout=2)

        writer.close()
        await writer.wait_closed()


if __name__ == "__main__":
    unittest.main()
