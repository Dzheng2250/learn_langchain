"""Generic asyncio TCP transport for the Core JSON-RPC router."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from src.core.bus.router import PARSE_ERROR, RpcRouter, error_response
from src.core.transport.framing import FrameError, encode_ndjson, read_ndjson


@dataclass
class SocketRequestContext:
    """TCP-backed implementation of the handler request context."""

    writer: asyncio.StreamWriter
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    request_id: str | int | None = None
    close_after_response: bool = False

    async def send_notification(self, value: Any) -> None:
        """Write one server-initiated notification on this request connection."""
        await self._send(value)

    async def send_response(self, value: Any) -> None:
        """Write the final JSON-RPC response on this request connection."""
        await self._send(value)

    def request_close(self) -> None:
        """Mark the connection for closure after its final response."""
        self.close_after_response = True

    async def _send(self, value: Any) -> None:
        """Serialize one NDJSON frame while preventing concurrent write interleaving."""
        async with self.write_lock:
            self.writer.write(encode_ndjson(value))
            await self.writer.drain()


class SocketServer:
    """Accept TCP connections and delegate validated messages to a router.

    A malformed NDJSON frame closes only the connection that supplied it after
    returning a Parse Error. Other concurrent connections remain unaffected.
    """

    def __init__(
        self,
        host: str,
        port: int,
        router: RpcRouter,
        *,
        max_message_bytes: int,
    ) -> None:
        self.host = host
        self.port = port
        self.router = router
        self.max_message_bytes = max_message_bytes
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task] = set()
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self) -> int:
        """Start accepting connections and return the bound port."""
        self._server = await asyncio.start_server(
            self._handle_connection,
            self.host,
            self.port,
            limit=self.max_message_bytes + 1,
        )
        if self._server.sockets:
            self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def close(self, timeout_seconds: float) -> None:
        """Stop accepting connections, close client streams, then drain requests."""
        if self._server is not None:
            self._server.close()

        # Connection tasks otherwise remain blocked waiting for the next frame,
        # while close() waits for those same tasks before closing their writer.
        # Closing the streams first releases idle readers without cancelling a
        # handler that is already executing.
        writers = list(self._writers)
        for writer in writers:
            writer.close()

        current = asyncio.current_task()
        active = [task for task in self._tasks if task is not current and not task.done()]
        if active:
            _done, pending = await asyncio.wait(active, timeout=timeout_seconds)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        if writers:
            await asyncio.gather(
                *(self._wait_closed(writer) for writer in writers),
                return_exceptions=True,
            )
        if self._server is not None:
            await self._server.wait_closed()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Read, dispatch, and respond to frames from one client connection."""
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        self._writers.add(writer)
        context = SocketRequestContext(writer)
        try:
            while not reader.at_eof():
                try:
                    raw = await read_ndjson(reader, self.max_message_bytes)
                except FrameError as exc:
                    await context.send_response(
                        error_response(None, PARSE_ERROR, "Parse error", str(exc))
                    )
                    break
                if raw is None:
                    break
                context.request_id = raw.get("id") if isinstance(raw, dict) else None
                # dispatch() owns protocol validation and business invocation;
                # transport only frames messages and serializes socket writes.
                response = await self.router.dispatch(raw, context)
                await context.send_response(response)
                if context.close_after_response:
                    break
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            await self._wait_closed(writer)
            if task is not None:
                self._tasks.discard(task)
            self._writers.discard(writer)

    async def _wait_closed(self, writer: asyncio.StreamWriter) -> None:
        """Wait for stream closure while tolerating peer disconnects."""
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass
