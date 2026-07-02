"""Agent RPC handlers."""

import asyncio
from uuid import uuid4

from src.core.agent.contracts import (
    AgentTurnRunner,
    ExecutionControl,
    SessionLifecycleController,
)
from src.core.bus.context import RequestContext
from src.core.bus.router import RpcInvalidParams, RpcRouter
from src.core.telemetry import record_error
from src.core.tracing import bind_trace_context, reset_trace_context
from src.ipc.models import (
    AgentEventNotification,
    ApprovalResolveParams,
    ChatParams,
    SessionDeleteParams,
    SessionParams,
    SessionResumeParams,
    ResourceActivityScopeParams,
    ResourceActivityListParams,
)


class AgentHandlers:
    """Adapt Agent application services to validated RPC methods."""

    def __init__(
        self,
        agent_service: AgentTurnRunner,
        session_service: SessionLifecycleController | None = None,
        approval_service=None,
        resource_activity_service=None,
    ) -> None:
        self.agent_service = agent_service
        self.session_service = session_service or agent_service
        self.approval_service = approval_service
        self.resource_activity_service = resource_activity_service

    def register(self, router: RpcRouter) -> None:
        """Expose chat and explicit Session recovery methods."""
        router.register("agent.chat", ChatParams, self.chat)
        router.register("session.status", SessionParams, self.session_status)
        router.register("session.resume", SessionResumeParams, self.session_resume)
        router.register("session.discard", SessionParams, self.session_discard)
        router.register("session.delete", SessionDeleteParams, self.session_delete)
        router.register("session.reset", SessionParams, self.session_reset)
        if self.resource_activity_service is not None:
            router.register("resource_activity.summary", ResourceActivityScopeParams, self.resource_activity_summary)
            router.register("resource_activity.list", ResourceActivityListParams, self.resource_activity_list)
        if self.approval_service is not None:
            router.register("approval.list", SessionParams, self.approval_list)
            router.register("approval.resolve", ApprovalResolveParams, self.approval_resolve)

    async def resource_activity_summary(self, params: ResourceActivityScopeParams, _context: RequestContext) -> dict:
        try:
            return await asyncio.to_thread(
                self.resource_activity_service.summary,
                **params.model_dump(exclude={"auth_token"}),
            )
        except ValueError as exc:
            raise RpcInvalidParams(str(exc)) from exc

    async def resource_activity_list(self, params: ResourceActivityListParams, _context: RequestContext) -> dict:
        try:
            return await asyncio.to_thread(
                self.resource_activity_service.list,
                **params.model_dump(exclude={"auth_token"}),
            )
        except ValueError as exc:
            raise RpcInvalidParams(str(exc)) from exc
    async def approval_list(self, params: SessionParams, _context: RequestContext) -> dict:
        return await asyncio.to_thread(
            self.approval_service.list_pending,
            params.workspace_root,
            params.session_name,
        )

    async def approval_resolve(self, params: ApprovalResolveParams, context: RequestContext) -> dict:
        resume_value = await asyncio.to_thread(
            self.approval_service.prepare_response,
            params.workspace_root,
            params.session_name,
            params.request_id,
            params.response,
        )
        run_id = uuid4().hex
        control = ExecutionControl()
        on_event = self._notification_callback(context, run_id, control)
        return await self.agent_service.resume_execution(
            params.workspace_root,
            params.session_name,
            "",
            on_event,
            run_id=run_id,
            control=control,
            resume_value=resume_value,
        )

    async def chat(self, params: ChatParams, context: RequestContext) -> dict:
        """Execute one Turn and bridge worker events to RPC notifications."""
        run_id = uuid4().hex
        control = ExecutionControl()
        on_event = self._notification_callback(context, run_id, control)
        token = bind_trace_context(run_id=run_id)
        try:
            return await self.agent_service.run_turn(
                params.workspace_root,
                params.session_name,
                params.message,
                on_event,
                run_id=run_id,
                control=control,
                goal_mode=params.goal_mode,
            )
        finally:
            reset_trace_context(token)

    async def session_status(self, params: SessionParams, _context: RequestContext) -> dict:
        """Return the recoverable execution state for one Session."""
        return await asyncio.to_thread(
            self.session_service.session_status,
            params.workspace_root,
            params.session_name,
        )

    async def session_discard(self, params: SessionParams, _context: RequestContext) -> dict:
        """Discard one pending execution without deleting its audit history."""
        return await asyncio.to_thread(
            self.session_service.discard_pending,
            params.workspace_root,
            params.session_name,
        )

    async def session_delete(self, params: SessionDeleteParams, _context: RequestContext) -> dict:
        """Archive or permanently delete one Session."""
        return await asyncio.to_thread(
            self.session_service.delete_session,
            params.workspace_root,
            params.session_name,
            hard_delete=params.hard_delete,
        )

    async def session_reset(self, params: SessionParams, _context: RequestContext) -> dict:
        """Rebuild recent_messages from archived message history."""
        return await asyncio.to_thread(
            self.session_service.reset_session,
            params.workspace_root,
            params.session_name,
        )

    async def session_resume(self, params: SessionResumeParams, context: RequestContext) -> dict:
        """Resume one pending execution and stream its new Slice events."""
        run_id = uuid4().hex
        control = ExecutionControl()
        on_event = self._notification_callback(context, run_id, control)
        token = bind_trace_context(run_id=run_id)
        try:
            return await self.agent_service.resume_execution(
                params.workspace_root,
                params.session_name,
                params.instruction,
                on_event,
                run_id=run_id,
                control=control,
            )
        finally:
            reset_trace_context(token)

    def _notification_callback(
        self,
        context: RequestContext,
        run_id: str,
        control: ExecutionControl,
    ):
        """Deliver terminal state before querying its non-critical activity summary."""
        loop = asyncio.get_running_loop()
        notification_failed = False
        activity_announced = False

        def send(notification) -> None:
            future = asyncio.run_coroutine_threadsafe(
                context.send_notification(notification), loop
            )
            future.result()

        def on_event(item: dict) -> None:
            nonlocal notification_failed, activity_announced
            if notification_failed:
                return
            notification = AgentEventNotification(params={
                "request_id": context.request_id,
                "run_id": run_id,
                "event": item["event"],
                "data": item["data"],
            })
            try:
                send(notification)
            except Exception as exc:
                notification_failed = True
                control.pause_after_slice.set()
                record_error(
                    "agent_handler", "stream_notification", exc,
                    "Stopped streaming notifications after client delivery failed.",
                    {"request_id": context.request_id, "run_id": run_id},
                    event_type="stream_notification_failed",
                )
                return

            if (
                activity_announced
                or item["event"] not in {"done", "paused", "error"}
                or self.resource_activity_service is None
            ):
                return
            activity_announced = True
            try:
                summary = self.resource_activity_service.summary_for_run(run_id)
                send(AgentEventNotification(params={
                    "request_id": context.request_id,
                    "run_id": run_id,
                    "event": "resource_activity_summary",
                    "data": {"schema_version": 1, "run_id": run_id, "summary": summary},
                }))
            except Exception as exc:
                record_error(
                    "agent_handler", "resource_activity_summary", exc,
                    "Resource activity summary delivery failed.", {"run_id": run_id},
                )

        return on_event