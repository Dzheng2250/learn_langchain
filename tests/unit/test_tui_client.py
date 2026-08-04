import asyncio
import json
import unittest

from src.tui.client import AsyncCoreClient, CoreProtocolError


class AsyncCoreClientFrameTest(unittest.IsolatedAsyncioTestCase):
    async def test_response_larger_than_asyncio_default_limit_is_supported(self):
        payload = "x" * 100_000

        async def handle(reader, writer):
            await reader.readline()
            writer.write((json.dumps({
                "jsonrpc": "2.0",
                "id": "tui-1",
                "result": {"payload": payload},
            }) + "\n").encode("utf-8"))
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = AsyncCoreClient("127.0.0.1", port, timeout=2)
        try:
            await client.connect()
            result = await client.request("session.history", {})
        finally:
            await client.close()
            server.close()
            await server.wait_closed()

        self.assertEqual(payload, result["payload"])

    async def test_oversized_response_becomes_protocol_error(self):
        async def handle(reader, writer):
            await reader.readline()
            writer.write((json.dumps({
                "jsonrpc": "2.0",
                "id": "tui-1",
                "result": {"payload": "x" * 4_000},
            }) + "\n").encode("utf-8"))
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = AsyncCoreClient(
            "127.0.0.1",
            port,
            timeout=2,
            max_message_bytes=1_024,
        )
        try:
            await client.connect()
            with self.assertRaisesRegex(CoreProtocolError, "frame limit"):
                await client.request("session.history", {})
        finally:
            await client.close()
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
