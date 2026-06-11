"""Agent RPC handlers."""

import asyncio
from typing import Callable, Protocol
from uuid import uuid4

from src.core.bus.context import RequestContext
from src.core.bus.router import RpcRouter
from src.ipc.models import AgentEventNotification, ChatParams


class AgentTurnRunner(Protocol):
    def run_turn(
        self,
        workspace_root: str,
        session_name: str,
        user_input: str,
        on_event: Callable[[dict], None] | None = None,
        *,
        run_id: str | None = None,
    ) -> dict:
        """Execute one agent turn."""


class AgentHandlers:
    """Adapt agent application services to RPC methods."""

    def __init__(self, agent_service: AgentTurnRunner) -> None:
        self.agent_service = agent_service

    def register(self, router: RpcRouter) -> None:
        router.register("agent.chat", ChatParams, self.chat)

    async def chat(self, params: ChatParams, context: RequestContext) -> dict:
        loop = asyncio.get_running_loop()
        run_id = uuid4().hex

        def on_event(item: dict) -> None:
            notification = AgentEventNotification(
                params={
                    "request_id": context.request_id,
                    "run_id": run_id,
                    "event": item["event"],
                    "data": item["data"],
                }
            )
            future = asyncio.run_coroutine_threadsafe(
                context.send_notification(notification),
                loop,
            )
            try:
                future.result()
            except Exception:
                # A disconnected client must not cancel a turn already executing in Core.
                pass

        return await asyncio.to_thread(
            self.agent_service.run_turn,
            params.workspace_root,
            params.session_name,
            params.message,
            on_event,
            run_id=run_id,
        )
