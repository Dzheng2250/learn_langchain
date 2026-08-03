import asyncio
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from src.core.handlers.agent import AgentHandlers
from src.core.handlers.core import CoreHandlers
from src.core.bus.router import INVALID_PARAMS, RpcRouter
from src.ipc.models import (
    ApprovalModeSetParams, ChatParams, PingParams,
    ResourceActivityListParams, ResourceActivityScopeParams,
    SessionDeleteParams, SessionHistoryParams, SessionParams, ShutdownParams,
)


class FakeRequestContext:
    def __init__(self):
        self.request_id = "request-1"
        self.notifications = []
        self.close_requested = False

    async def send_notification(self, value):
        self.notifications.append(value)

    def request_close(self):
        self.close_requested = True


class FakeAgentService:
    async def run_turn(
        self,
        workspace_root,
        session_name,
        message,
        on_event,
        *,
        run_id=None,
        control=None,
        goal_mode=False,
    ):
        await asyncio.to_thread(on_event, {"event": "token", "data": {"content": "hello"}})
        return {
            "status": "ok",
            "run_id": run_id,
            "workspace_root": workspace_root,
            "session_name": session_name,
            "message": message,
            "goal_mode": goal_mode,
        }

    def delete_session(self, workspace_root, session_name, *, hard_delete=False):
        return {
            "status": "deleted" if hard_delete else "archived",
            "workspace_root": workspace_root,
            "session_name": session_name,
            "hard_delete": hard_delete,
        }


class FakeSessionService:
    def delete_session(self, workspace_root, session_name, *, hard_delete=False):
        return {
            "status": "session-service",
            "workspace_root": workspace_root,
            "session_name": session_name,
            "hard_delete": hard_delete,
        }


class FakeSessionHistoryService:
    def __init__(self):
        self.calls = []

    def list_history(self, workspace_root, session_name, **kwargs):
        self.calls.append((workspace_root, session_name, kwargs))
        return {
            "schema_version": 1,
            "session_name": session_name,
            "archived": False,
            "turns": [],
            "next_before_turn": None,
            "has_more": False,
        }


class CoreHandlersTest(unittest.IsolatedAsyncioTestCase):
    async def test_ping_returns_health_data(self):
        handlers = CoreHandlers(asyncio.Event(), server_version="test")
        result = await handlers.ping(PingParams(auth_token="token"), FakeRequestContext())
        self.assertEqual("ok", result["status"])
        self.assertEqual("test", result["server_version"])

    async def test_session_history_delegates_validated_pagination(self):
        history = FakeSessionHistoryService()
        handlers = AgentHandlers(
            FakeAgentService(),
            session_history_service=history,
        )

        result = await handlers.session_history(
            SessionHistoryParams(
                auth_token="token",
                workspace_root="workspace",
                session_name="history",
                before_turn=20,
                limit_turns=30,
            ),
            FakeRequestContext(),
        )

        self.assertEqual("history", result["session_name"])
        self.assertEqual(
            [("workspace", "history", {"before_turn": 20, "limit_turns": 30})],
            history.calls,
        )

    async def test_session_history_params_bound_complete_turn_page_size(self):
        params = SessionHistoryParams(
            auth_token="token",
            workspace_root="workspace",
        )
        self.assertEqual(30, params.limit_turns)
        self.assertIsNone(params.before_turn)
        with self.assertRaises(ValidationError):
            SessionHistoryParams(
                auth_token="token",
                workspace_root="workspace",
                limit_turns=101,
            )
        with self.assertRaises(ValidationError):
            SessionHistoryParams(
                auth_token="token",
                workspace_root="workspace",
                before_turn=-1,
            )

    async def test_shutdown_requests_connection_close_and_app_shutdown(self):
        shutdown_event = asyncio.Event()
        handlers = CoreHandlers(shutdown_event)
        context = FakeRequestContext()
        result = await handlers.shutdown(ShutdownParams(auth_token="token"), context)
        await asyncio.sleep(0)
        self.assertTrue(context.close_requested)
        self.assertTrue(shutdown_event.is_set())
        self.assertEqual("shutting_down", result["status"])


class AgentHandlersTest(unittest.IsolatedAsyncioTestCase):
    async def test_approval_mode_get_and_set_share_the_generic_rpc_contract(self):
        class ApprovalModes:
            def get_mode(self, workspace_root, session_name):
                return {
                    "schema_version": 1,
                    "default_mode": "manual",
                    "override_mode": None,
                    "effective_mode": "manual",
                    "supported_modes": ["manual", "accept_all"],
                    "pending_count": 0,
                    "scope": [workspace_root, session_name],
                }

            def set_mode(
                self,
                workspace_root,
                session_name,
                mode,
                *,
                acknowledge_risk=False,
            ):
                return {
                    "effective_mode": mode,
                    "acknowledged": acknowledge_risk,
                    "scope": [workspace_root, session_name],
                }

        handlers = AgentHandlers(FakeAgentService(), approval_service=ApprovalModes())
        context = FakeRequestContext()
        current = await handlers.approval_mode_get(
            SessionParams(
                auth_token="token",
                workspace_root="workspace",
                session_name="session",
            ),
            context,
        )
        changed = await handlers.approval_mode_set(
            ApprovalModeSetParams(
                auth_token="token",
                workspace_root="workspace",
                session_name="session",
                mode="accept_all",
                acknowledge_risk=True,
            ),
            context,
        )

        self.assertEqual("manual", current["effective_mode"])
        self.assertEqual("accept_all", changed["effective_mode"])
        self.assertTrue(changed["acknowledged"])

    async def test_chat_adapts_service_events_to_notifications(self):
        handlers = AgentHandlers(FakeAgentService())
        context = FakeRequestContext()
        result = await handlers.chat(
            ChatParams(
                auth_token="token",
                workspace_root=".",
                session_name="session",
                message="hello",
                goal_mode=True,
            ),
            context,
        )
        self.assertEqual("ok", result["status"])
        self.assertTrue(result["goal_mode"])
        self.assertEqual(1, len(context.notifications))
        params = context.notifications[0].params
        self.assertEqual("request-1", params["request_id"])
        self.assertEqual("token", params["event"])

    async def test_notification_and_terminal_retry_failures_are_both_recorded(self):
        completed = False

        class FailingContext(FakeRequestContext):
            def __init__(self):
                super().__init__()
                self.attempts = 0

            async def send_notification(self, _value):
                self.attempts += 1
                raise ConnectionError("client disconnected")

        class MultiEventService:
            async def run_turn(
                self,
                _workspace,
                _session,
                _message,
                on_event,
                *,
                run_id=None,
                control=None,
                goal_mode=False,
            ):
                nonlocal completed
                await asyncio.to_thread(on_event, {"event": "token", "data": {}})
                await asyncio.to_thread(on_event, {"event": "done", "data": {}})
                self.control = control
                completed = True
                return {"status": "ok", "run_id": run_id}

        context = FailingContext()
        service = MultiEventService()
        with patch("src.core.handlers.agent.record_error") as record_error:
            result = await AgentHandlers(service).chat(
                ChatParams(auth_token="token", workspace_root=".", session_name="session", message="hello"),
                context,
            )

        self.assertEqual("ok", result["status"])
        self.assertTrue(completed)
        self.assertEqual(2, context.attempts)
        self.assertTrue(service.control.pause_after_slice.is_set())
        self.assertEqual(2, record_error.call_count)
        self.assertEqual(
            "stream_notification_failed",
            record_error.call_args_list[0].kwargs["event_type"],
        )
        self.assertEqual(
            "stream_notification_terminal_retry_failed",
            record_error.call_args_list[1].kwargs["event_type"],
        )

    async def test_resource_activity_api_and_terminal_event_share_core_contract(self):
        class TerminalService(FakeAgentService):
            async def run_turn(self, workspace_root, session_name, message, on_event, **kwargs):
                await asyncio.to_thread(on_event, {"event": "done", "data": {"status": "ok"}})
                return {"status": "ok", "run_id": kwargs.get("run_id")}
        class Activities:
            def summary_for_run(self, run_id):
                return {"schema_version": 1, "scope": {"run_id": run_id}, "reads": {}, "changes": {}, "evidence": {}, "truncated": False}
            def summary(self, **params): return {"schema_version": 1, "scope": params}
            def list(self, **params): return {"schema_version": 1, "items": [], "next_cursor": None, "has_more": False, "filters": params}
        activities=Activities(); handlers=AgentHandlers(TerminalService(), resource_activity_service=activities)
        context=FakeRequestContext()
        await handlers.chat(ChatParams(auth_token="token",workspace_root=".",message="hello"),context)
        for _ in range(20):
            if len(context.notifications) >= 2:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(["done", "resource_activity_summary"], [item.params["event"] for item in context.notifications])
        summary=await handlers.resource_activity_summary(ResourceActivityScopeParams(auth_token="token",execution_id="exec-1"),context)
        listing=await handlers.resource_activity_list(ResourceActivityListParams(auth_token="token",execution_id="exec-1",limit=10),context)
        self.assertEqual("exec-1",summary["scope"]["execution_id"])
        self.assertEqual([],listing["items"])
    async def test_resource_summary_failure_does_not_hide_terminal_event(self):
        class TerminalService(FakeAgentService):
            async def run_turn(self, workspace_root, session_name, message, on_event, **kwargs):
                await asyncio.to_thread(on_event, {"event": "done", "data": {"status": "ok"}})
                return {"status": "ok"}

        class BrokenActivities:
            def summary_for_run(self, _run_id):
                raise RuntimeError("ledger unavailable")

        context = FakeRequestContext()
        with patch("src.core.handlers.agent.record_error") as record_error:
            await AgentHandlers(
                TerminalService(), resource_activity_service=BrokenActivities()
            ).chat(
                ChatParams(auth_token="token", workspace_root=".", message="hello"),
                context,
            )
        self.assertEqual(["done"], [item.params["event"] for item in context.notifications])
        record_error.assert_called_once()
    async def test_resource_summary_is_retried_until_delivery_succeeds(self):
        class RepeatedTerminalService(FakeAgentService):
            async def run_turn(self, workspace_root, session_name, message, on_event, **kwargs):
                await asyncio.to_thread(on_event, {"event": "done", "data": {"status": "ok"}})
                await asyncio.to_thread(on_event, {"event": "done", "data": {"status": "ok"}})
                return {"status": "ok", "run_id": kwargs.get("run_id")}

        class FlakyActivities:
            def __init__(self):
                self.calls = 0

            def summary_for_run(self, run_id):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("temporary ledger failure")
                return {
                    "schema_version": 1,
                    "scope": {"run_id": run_id},
                    "reads": {}, "changes": {}, "evidence": {}, "truncated": False,
                }

        activities = FlakyActivities()
        context = FakeRequestContext()
        with patch("src.core.handlers.agent.record_error") as record_error:
            await AgentHandlers(
                RepeatedTerminalService(), resource_activity_service=activities
            ).chat(
                ChatParams(auth_token="token", workspace_root=".", message="hello"),
                context,
            )

        self.assertEqual(2, activities.calls)
        self.assertEqual(
            ["done", "done", "resource_activity_summary"],
            [item.params["event"] for item in context.notifications],
        )
        record_error.assert_called_once()

    async def test_resource_activity_unknown_session_is_invalid_params(self):
        class Activities:
            def summary(self, **_params):
                raise ValueError("Session not found")
            def list(self, **_params):
                raise ValueError("Session not found")

        router = RpcRouter("token")
        AgentHandlers(FakeAgentService(), resource_activity_service=Activities()).register(router)
        response = await router.dispatch({
            "jsonrpc": "2.0",
            "id": "resource-invalid",
            "method": "resource_activity.summary",
            "params": {
                "auth_token": "token", "workspace_root": ".",
                "session_name": "missing", "turn_index": 1,
            },
        }, FakeRequestContext())
        self.assertEqual(INVALID_PARAMS, response.error.code)
        self.assertIn("Session not found", str(response.error.data))
    async def test_session_domain_value_error_is_invalid_params(self):
        class InvalidSessionService:
            def session_status(self, _workspace_root, _session_name):
                raise ValueError("Session not found")

        router = RpcRouter("token")
        AgentHandlers(FakeAgentService(), InvalidSessionService()).register(router)
        response = await router.dispatch({
            "jsonrpc": "2.0", "id": "session-invalid", "method": "session.status",
            "params": {
                "auth_token": "token", "workspace_root": ".", "session_name": "missing",
            },
        }, FakeRequestContext())

        self.assertEqual(INVALID_PARAMS, response.error.code)
        self.assertIn("Session not found", str(response.error.data))

    async def test_session_resume_domain_value_error_is_invalid_params(self):
        class InvalidResumeService(FakeAgentService):
            async def resume_execution(self, *_args, **_kwargs):
                raise ValueError("Workspace not found")

        router = RpcRouter("token")
        AgentHandlers(InvalidResumeService()).register(router)
        response = await router.dispatch({
            "jsonrpc": "2.0", "id": "resume-invalid", "method": "session.resume",
            "params": {
                "auth_token": "token", "workspace_root": ".", "session_name": "missing",
            },
        }, FakeRequestContext())

        self.assertEqual(INVALID_PARAMS, response.error.code)
        self.assertIn("Workspace not found", str(response.error.data))

    async def test_approval_prepare_value_error_is_invalid_params(self):
        class InvalidApprovalService:
            def prepare_response(self, *_args, **_kwargs):
                raise ValueError("Approval request not found")

        router = RpcRouter("token")
        AgentHandlers(
            FakeAgentService(), approval_service=InvalidApprovalService()
        ).register(router)
        response = await router.dispatch({
            "jsonrpc": "2.0", "id": "approval-invalid", "method": "approval.resolve",
            "params": {
                "auth_token": "token", "workspace_root": ".", "session_name": "default",
                "request_id": "missing", "response": "deny_once",
            },
        }, FakeRequestContext())

        self.assertEqual(INVALID_PARAMS, response.error.code)
        self.assertIn("Approval request not found", str(response.error.data))

    async def test_approval_resume_value_error_is_invalid_params(self):
        class ApprovalService:
            def prepare_response(self, *_args, **_kwargs):
                return {"request_id": "approval-1", "allowed": True}

        class InvalidResumeService(FakeAgentService):
            async def resume_execution(self, *_args, **_kwargs):
                raise ValueError("Pending execution not found")

        router = RpcRouter("token")
        AgentHandlers(
            InvalidResumeService(), approval_service=ApprovalService()
        ).register(router)
        response = await router.dispatch({
            "jsonrpc": "2.0", "id": "approval-resume-invalid",
            "method": "approval.resolve",
            "params": {
                "auth_token": "token", "workspace_root": ".", "session_name": "default",
                "request_id": "approval-1", "response": "allow_once",
            },
        }, FakeRequestContext())

        self.assertEqual(INVALID_PARAMS, response.error.code)
        self.assertIn("Pending execution not found", str(response.error.data))

    async def test_session_delete_passes_hard_delete_flag(self):
        handlers = AgentHandlers(FakeAgentService())
        result = await handlers.session_delete(
            SessionDeleteParams(
                auth_token="token",
                workspace_root=".",
                session_name="old",
                hard_delete=True,
            ),
            FakeRequestContext(),
        )

        self.assertEqual("deleted", result["status"])
        self.assertTrue(result["hard_delete"])

    async def test_session_delete_can_use_separate_session_service(self):
        handlers = AgentHandlers(FakeAgentService(), FakeSessionService())
        result = await handlers.session_delete(
            SessionDeleteParams(
                auth_token="token",
                workspace_root=".",
                session_name="old",
                hard_delete=True,
            ),
            FakeRequestContext(),
        )

        self.assertEqual("session-service", result["status"])
        self.assertTrue(result["hard_delete"])


if __name__ == "__main__":
    unittest.main()
