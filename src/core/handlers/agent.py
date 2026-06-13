"""Agent RPC handlers."""

import asyncio
from uuid import uuid4

from src.core.agent.contracts import AgentTurnRunner
from src.core.bus.context import RequestContext
from src.core.bus.router import RpcRouter
from src.core.hooks.events import record_error
from src.ipc.models import AgentEventNotification, ChatParams


class AgentHandlers:
    """Adapt agent application services to RPC methods."""

    def __init__(self, agent_service: AgentTurnRunner) -> None:
        self.agent_service = agent_service

    def register(self, router: RpcRouter) -> None:
        """Expose the validated ``agent.chat`` RPC method."""
        router.register("agent.chat", ChatParams, self.chat)

    async def chat(self, params: ChatParams, context: RequestContext) -> dict:
        """Execute one Turn and bridge worker events to RPC notifications."""
        loop = asyncio.get_running_loop()
        run_id = uuid4().hex
        notification_failed = False

        def on_event(item: dict) -> None:
            """Forward one worker-thread stream item to the request connection."""
            # AgentTurnService executes in a worker thread. Marshal each stream
            # event back onto the Core asyncio loop before touching the socket.
            nonlocal notification_failed
            if notification_failed:
                return
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
            except Exception as exc:
                # A broken client stream must not cancel a turn that may still
                # need to persist messages, context, and long-term memory.
                notification_failed = True
                record_error(
                    "agent_handler",
                    "stream_notification",
                    exc,
                    "Stopped streaming notifications after client delivery failed.",
                    {
                        "request_id": context.request_id,
                        "run_id": run_id,
                    },
                    event_type="stream_notification_failed",
                )

        return await self.agent_service.run_turn(
            params.workspace_root,
            params.session_name,
            params.message,
            on_event,
            run_id=run_id,
        )
