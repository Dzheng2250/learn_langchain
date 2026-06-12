"""Core lifecycle RPC handlers."""

import asyncio
import time

from src.core.bus.context import RequestContext
from src.core.bus.router import RpcRouter
from src.ipc.models import PingParams, ShutdownParams


class CoreHandlers:
    """Expose daemon health and shutdown operations."""

    def __init__(
        self,
        shutdown_event: asyncio.Event,
        *,
        server_version: str = "0.1.0",
    ) -> None:
        self.shutdown_event = shutdown_event
        self.server_version = server_version
        self.started_at = time.monotonic()

    def register(self, router: RpcRouter) -> None:
        """Expose health and graceful-shutdown lifecycle methods."""
        router.register("core.ping", PingParams, self.ping)
        router.register("core.shutdown", ShutdownParams, self.shutdown)

    async def ping(self, _params: PingParams, _context: RequestContext) -> dict:
        """Return daemon version and uptime without touching Agent services."""
        return {
            "status": "ok",
            "server_version": self.server_version,
            "uptime_ms": int((time.monotonic() - self.started_at) * 1000),
        }

    async def shutdown(self, _params: ShutdownParams, context: RequestContext) -> dict:
        """Request graceful app shutdown after the final RPC response."""
        context.request_close()
        asyncio.get_running_loop().call_soon(self.shutdown_event.set)
        return {"status": "shutting_down"}
