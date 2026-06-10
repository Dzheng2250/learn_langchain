"""Async TCP JSON-RPC server for the Core daemon."""

import asyncio
import os
import time
from dataclasses import dataclass, field

from src.core.agent.service import AgentTurnService
from src.core.bus.framing import FrameError, encode_ndjson, read_ndjson
from src.ipc.auth import ensure_runtime_dir, pid_path
from src.ipc.models import (
    AgentEventNotification,
    ChatParams,
    PingParams,
    ShutdownParams,
)
from src.core.bus.router import PARSE_ERROR, RpcRouter, error_response
from src.config.settings import (
    CORE_HOST,
    CORE_MAX_MESSAGE_BYTES,
    CORE_PORT,
    CORE_SHUTDOWN_TIMEOUT_SECONDS,
)


@dataclass
class ConnectionContext:
    writer: asyncio.StreamWriter
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    request_id: str | int | None = None
    close_after_response: bool = False

    async def send(self, value) -> None:
        async with self.write_lock:
            self.writer.write(encode_ndjson(value))
            await self.writer.drain()


class CoreRpcServer:
    """Host validated Core methods over local TCP."""

    def __init__(
        self,
        auth_token: str,
        *,
        host: str = CORE_HOST,
        port: int = CORE_PORT,
        agent_service: AgentTurnService | None = None,
        manage_runtime_files: bool = True,
    ) -> None:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Core daemon may only bind to a loopback address")
        self.host = host
        self.port = port
        self.agent_service = agent_service or AgentTurnService()
        self.manage_runtime_files = manage_runtime_files
        self.router = RpcRouter(auth_token)
        self.router.register("core.ping", PingParams, self._ping)
        self.router.register("core.shutdown", ShutdownParams, self._shutdown)
        self.router.register("agent.chat", ChatParams, self._chat)
        self.started_at = time.monotonic()
        self.shutdown_event = asyncio.Event()
        self.server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task] = set()
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self) -> None:
        self.agent_service.initialize()
        if self.manage_runtime_files:
            ensure_runtime_dir()
        self.server = await asyncio.start_server(
            self._handle_connection,
            self.host,
            self.port,
            limit=CORE_MAX_MESSAGE_BYTES + 1,
        )
        if self.server.sockets:
            self.port = self.server.sockets[0].getsockname()[1]
        if self.manage_runtime_files:
            pid_path().write_text(str(os.getpid()), encoding="ascii")

    async def serve_until_shutdown(self) -> None:
        await self.start()
        await self.shutdown_event.wait()
        await self.close()

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        for writer in list(self._writers):
            writer.close()
        current = asyncio.current_task()
        active = [task for task in self._tasks if task is not current and not task.done()]
        if active:
            done, pending = await asyncio.wait(active, timeout=CORE_SHUTDOWN_TIMEOUT_SECONDS)
            for task in pending:
                task.cancel()
        self.agent_service.close()
        if self.manage_runtime_files:
            try:
                pid_path().unlink(missing_ok=True)
            except OSError:
                pass

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        self._writers.add(writer)
        context = ConnectionContext(writer)
        try:
            while not reader.at_eof():
                try:
                    raw = await read_ndjson(reader, CORE_MAX_MESSAGE_BYTES)
                except FrameError as exc:
                    await context.send(error_response(None, PARSE_ERROR, "Parse error", str(exc)))
                    break
                if raw is None:
                    break
                context.request_id = raw.get("id") if isinstance(raw, dict) else None
                response = await self.router.dispatch(raw, context)
                await context.send(response)
                if context.close_after_response:
                    break
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            if task is not None:
                self._tasks.discard(task)
            self._writers.discard(writer)

    async def _ping(self, _params: PingParams, _context: ConnectionContext) -> dict:
        return {
            "status": "ok",
            "server_version": "0.1.0",
            "uptime_ms": int((time.monotonic() - self.started_at) * 1000),
        }

    async def _shutdown(self, _params: ShutdownParams, _context: ConnectionContext) -> dict:
        _context.close_after_response = True
        asyncio.get_running_loop().call_soon(self.shutdown_event.set)
        return {"status": "shutting_down"}

    async def _chat(self, params: ChatParams, context: ConnectionContext) -> dict:
        loop = asyncio.get_running_loop()

        def on_event(item: dict) -> None:
            notification = AgentEventNotification(
                params={
                    "request_id": getattr(context, "request_id", ""),
                    "run_id": result_run_id,
                    "event": item["event"],
                    "data": item["data"],
                }
            )
            future = asyncio.run_coroutine_threadsafe(context.send(notification), loop)
            try:
                future.result()
            except Exception:
                pass

        # The request id is assigned by dispatch_connection before handler execution.
        result_run_id = __import__("uuid").uuid4().hex
        return await asyncio.to_thread(
            self.agent_service.run_turn,
            params.session_id,
            params.message,
            on_event,
            run_id=result_run_id,
        )
