import unittest
from pathlib import Path
from uuid import uuid4

from src.core.agent.models import RunLimits, StopReason
from src.core.diagnostics import DiagnosticTurnService
from src.core.workspace.models import SessionContext, WorkspaceContext


class FakeStore:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.closed = False

    def load_context(self, _session):
        if self.fail:
            raise RuntimeError("load failed")
        return "state", 3

    def close(self):
        self.closed = True


class DiagnosticTurnServiceTest(unittest.TestCase):
    def _session(self):
        return SessionContext(
            session_id=uuid4(),
            session_name="default",
            workspace=WorkspaceContext(uuid4(), Path("workspace")),
        )

    def test_streams_diagnostic_token_and_done_without_mutating_turn(self):
        store = FakeStore()
        service = DiagnosticTurnService(
            session_store=store,
            run_limits=RunLimits(),
        )

        events = list(
            service.stream_unconfigured_turn(
                self._session(),
                "run-1",
                ("LEARN_AGENT_LLM_API_KEY",),
            )
        )

        self.assertEqual(["token", "done"], [event["event"] for event in events])
        self.assertIn("content", events[0]["data"])
        done = events[-1]["data"]
        self.assertEqual("run-1", done["run_id"])
        self.assertEqual("ok", done["status"])
        self.assertEqual(StopReason.LLM_NOT_CONFIGURED.value, done["stop_reason"])
        self.assertEqual(0, done["tool_call_count"])
        self.assertFalse(store.closed)

    def test_returns_error_event_when_state_load_fails(self):
        store = FakeStore(fail=True)
        service = DiagnosticTurnService(
            session_store=store,
            run_limits=RunLimits(),
        )

        events = list(
            service.stream_unconfigured_turn(
                self._session(),
                "run-2",
                ("LEARN_AGENT_LLM_API_KEY",),
            )
        )

        self.assertEqual(["error"], [event["event"] for event in events])
        self.assertEqual("diagnostic_turn_failed", events[0]["data"]["type"])
        self.assertEqual(StopReason.TURN_ERROR.value, events[0]["data"]["stop_reason"])
        self.assertEqual("run-2", events[0]["data"]["run_id"])
        self.assertFalse(store.closed)


if __name__ == "__main__":
    unittest.main()
