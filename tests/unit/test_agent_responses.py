import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from src.core.agent.models import StopReason
from src.core.agent.responses import (
    archived_session_event,
    completed_turn_event,
    failed_turn_event,
    idle_resume_event,
    paused_turn_event,
    pending_execution_event,
)
from src.core.state.types import ExecutionStatus
from src.core.workspace.models import SessionContext, WorkspaceContext


class AgentResponsesTest(unittest.TestCase):
    def _session(self):
        return SessionContext(
            session_id=uuid4(),
            session_name="default",
            workspace=WorkspaceContext(uuid4(), Path("workspace")),
        )

    def test_pending_execution_event_preserves_recovery_fields(self):
        session = self._session()
        pending = SimpleNamespace(
            execution_id="exec-1",
            stop_reason=None,
            status=ExecutionStatus.PAUSED_BUDGET,
            goal_mode=True,
            resume_policy="continue",
        )

        event = pending_execution_event(session, "run-1", pending)

        self.assertEqual("done", event["event"])
        data = event["data"]
        self.assertEqual("paused", data["status"])
        self.assertEqual("exec-1", data["execution_id"])
        self.assertEqual(ExecutionStatus.PAUSED_BUDGET.value, data["stop_reason"])
        self.assertTrue(data["goal_mode"])
        self.assertEqual("continue", data["resume_policy"])

    def test_pending_action_required_event_points_to_recovery_rpc(self):
        session = self._session()
        pending = SimpleNamespace(
            execution_id="exec-recovery",
            stop_reason=StopReason.TOOL_RECOVERY_REQUIRED.value,
            status=ExecutionStatus.PAUSED_RECOVERY,
            goal_mode=False,
            resume_policy="action_required",
        )

        event = pending_execution_event(session, "run-recovery", pending)

        self.assertIn("tool_recovery.list/resolve", event["data"]["message"])
        self.assertEqual("action_required", event["data"]["resume_policy"])

    def test_archived_and_idle_events_are_terminal_done_events(self):
        session = self._session()

        archived = archived_session_event(session, "run-2")
        idle = idle_resume_event(session, "run-3")

        self.assertEqual("done", archived["event"])
        self.assertEqual("archived", archived["data"]["status"])
        self.assertEqual("done", idle["event"])
        self.assertEqual("idle", idle["data"]["status"])

    def test_completed_turn_event_contains_durability_and_maintenance_fields(self):
        session = self._session()
        execution = SimpleNamespace(execution_id="exec-2", goal_mode=True)
        finalization = SimpleNamespace(
            maintenance_status="pending",
            memory_status="pending",
            memory_request_explicit=True,
        )

        event = completed_turn_event(
            session=session,
            run_id="run-4",
            execution=execution,
            tool_call_count=7,
            slices_used=2,
            finalization=finalization,
            context_tokens=123,
        )

        data = event["data"]
        self.assertEqual("ok", data["status"])
        self.assertEqual(StopReason.COMPLETED.value, data["stop_reason"])
        self.assertEqual("committed", data["durability"])
        self.assertEqual("pending", data["maintenance_status"])
        self.assertEqual(123, data["context_tokens"])

    def test_paused_and_failed_events_use_stable_stop_reasons(self):
        session = self._session()

        paused = paused_turn_event(
            session=session,
            run_id="run-5",
            execution=None,
            stop_reason=StopReason.BUDGET_LIMIT.value,
            tool_call_count=3,
            slices_used=1,
            message="paused",
        )
        failed = failed_turn_event("run-6", "failed")

        self.assertEqual("paused", paused["data"]["status"])
        self.assertEqual(StopReason.BUDGET_LIMIT.value, paused["data"]["stop_reason"])
        self.assertEqual("error", failed["event"])
        self.assertEqual(StopReason.TURN_ERROR.value, failed["data"]["stop_reason"])


if __name__ == "__main__":
    unittest.main()
