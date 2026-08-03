"""Async TCP/NDJSON JSON-RPC client for the learn-agent Core daemon."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from src.config.settings import CORE_HOST, CORE_PORT

EventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class IpcError(Exception):
    """Base TUI IPC error."""


class CoreUnavailableError(IpcError):
    """Daemon is not running or connection was refused."""


class CoreConnectionInterruptedError(IpcError):
    """Connection lost mid-stream."""


class CoreProtocolError(IpcError):
    """Invalid JSON, encoding, or protocol violation."""


class CoreAuthenticationError(IpcError):
    """Authentication token rejected."""


class CoreRequestError(IpcError):
    """JSON-RPC error response."""


def safe_rpc_error_detail(data: Any) -> str:
    """Return useful JSON-RPC error details without echoing rejected input."""
    if isinstance(data, str):
        return " ".join(data.split())
    if not isinstance(data, list):
        return ""
    details = []
    for item in data[:3]:
        if not isinstance(item, dict):
            continue
        location = ".".join(str(part) for part in item.get("loc") or ())
        message = " ".join(str(item.get("msg") or "").split())
        if message:
            details.append(f"{location}: {message}" if location else message)
    return "; ".join(details)


class AsyncCoreClient:
    """Async TCP/NDJSON JSON-RPC client for one request-response cycle.

    Open a connection, send one request, receive 0..N agent.event
    notifications, then a final response, then close.
    """

    def __init__(
        self,
        host: str = CORE_HOST,
        port: int = CORE_PORT,
        timeout: float = 15.0,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        """Open a TCP connection to the Core daemon."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=self._timeout,
            )
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError) as exc:
            raise CoreUnavailableError(
                f"Cannot connect to {self._host}:{self._port}: {exc}"
            ) from exc

    async def close(self) -> None:
        """Close the TCP connection."""
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    async def request(
        self,
        method: str,
        params: dict[str, Any],
        on_event: EventCallback | None = None,
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and stream agent.event notifications.

        Args:
            method: RPC method name (e.g. ``"agent.chat"``).
            params: RPC parameters dict.
            on_event: Optional async callback invoked for each
                ``agent.event`` notification's ``params`` dict.

        Returns:
            The JSON-RPC result dict on success.

        Raises:
            CoreUnavailableError: connection failed.
            CoreConnectionInterruptedError: stream cut mid-response.
            CoreProtocolError: invalid protocol data.
            CoreAuthenticationError: auth token rejected (error code -32001).
            CoreRequestError: other JSON-RPC error.
        """
        if self._writer is None or self._reader is None:
            raise CoreUnavailableError("Not connected. Call connect() first.")

        request_id = "tui-1"
        request_body = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        raw_request = json.dumps(request_body, ensure_ascii=False)

        try:
            self._writer.write((raw_request + "\n").encode("utf-8"))
            await self._writer.drain()
        except OSError as exc:
            raise CoreConnectionInterruptedError(f"Write failed: {exc}") from exc

        while True:
            try:
                raw_line = await asyncio.wait_for(
                    self._reader.readline(),
                    timeout=self._timeout,
                )
            except asyncio.TimeoutError:
                raise CoreConnectionInterruptedError("Read timeout") from None
            except OSError as exc:
                raise CoreConnectionInterruptedError(f"Read failed: {exc}") from exc

            if not raw_line:
                raise CoreConnectionInterruptedError("Connection closed by server")

            try:
                message = json.loads(raw_line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise CoreProtocolError(f"Invalid message: {exc}") from exc

            if not isinstance(message, dict):
                raise CoreProtocolError("Message is not a JSON object")

            # Server-pushed event notification
            if message.get("method") == "agent.event":
                params_data = message.get("params", {})
                if on_event is not None:
                    result = on_event(params_data)
                    if isinstance(result, Awaitable):
                        await result
                continue

            # Final JSON-RPC response
            if message.get("id") == request_id:
                if "error" in message:
                    error = message["error"]
                    code = error.get("code", 0)
                    msg = error.get("message", "Unknown error")
                    if code == -32001:
                        raise CoreAuthenticationError(msg)
                    detail = safe_rpc_error_detail(error.get("data"))
                    suffix = f": {detail}" if detail else ""
                    raise CoreRequestError(f"[{code}] {msg}{suffix}")
                return message.get("result", {})

            # Unexpected message shape
            raise CoreProtocolError(f"Unexpected message: {message}")

    async def ping(self, auth_token: str = "") -> dict[str, Any]:
        """Send core.ping health check."""
        params: dict[str, Any] = {}
        if auth_token:
            params["auth_token"] = auth_token
        return await self.request("core.ping", params)
