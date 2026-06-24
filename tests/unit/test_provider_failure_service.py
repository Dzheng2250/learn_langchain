import unittest
from pathlib import Path
from uuid import uuid4

from src.core.errors.provider_failure import ProviderFailureService
from src.core.workspace.models import SessionContext, WorkspaceContext


class FakeExecution:
    execution_id = "exec-1"
    checkpoint_thread_id = "thread-1"
    goal_mode = True


class FakeExecutionRepository:
    def __init__(self):
        self.terminated = []

    def terminate(self, session, execution_id, reason):
        self.terminated.append((session, execution_id, reason))


class FakeMaintenanceRepository:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.jobs = []

    def enqueue(self, job):
        if self.fail:
            raise RuntimeError("enqueue failed")
        self.jobs.append(job)


class FakeScheduler:
    def __init__(self):
        self.wakes = 0

    def wake(self):
        self.wakes += 1


class ProviderFailureServiceTest(unittest.TestCase):
    def _session(self):
        return SessionContext(
            session_id=uuid4(),
            session_name="default",
            workspace=WorkspaceContext(uuid4(), Path("workspace")),
        )

    def test_terminate_execution_releases_session_and_queues_checkpoint_cleanup(self):
        execution_repository = FakeExecutionRepository()
        maintenance_repository = FakeMaintenanceRepository()
        scheduler = FakeScheduler()
        session = self._session()

        ProviderFailureService(
            execution_repository=execution_repository,
            maintenance_repository=maintenance_repository,
            maintenance_scheduler=scheduler,
        ).terminate_execution_after_error(session, FakeExecution(), "content_rejected")

        self.assertEqual([(session, "exec-1", "content_rejected")], execution_repository.terminated)
        self.assertEqual(1, len(maintenance_repository.jobs))
        self.assertEqual("checkpoint_cleanup:exec-1", maintenance_repository.jobs[0].dedupe_key)
        self.assertEqual(1, scheduler.wakes)

    def test_cleanup_enqueue_failure_does_not_raise_after_session_release(self):
        execution_repository = FakeExecutionRepository()

        ProviderFailureService(
            execution_repository=execution_repository,
            maintenance_repository=FakeMaintenanceRepository(fail=True),
            maintenance_scheduler=FakeScheduler(),
        ).terminate_execution_after_error(self._session(), FakeExecution(), "content_rejected")

        self.assertEqual("exec-1", execution_repository.terminated[0][1])

    def test_emit_terminal_provider_error_reports_recovered_done_event(self):
        session = self._session()
        item = {
            "data": {
                "message": "provider rejected this turn",
                "stop_reason": "turn_error",
                "error_category": "content_rejected",
                "error_action": "terminate",
                "retryable": False,
                "provider": "provider-a",
                "provider_code": "inspection_failed",
                "http_status": 400,
                "failure_source": "agent_turn",
                "failure_stage": "parent_model_provider",
                "failure_scope": "current_turn",
                "user_action": "revise_input_and_retry",
            }
        }

        events = list(
            ProviderFailureService().emit_terminal_provider_error(
                session,
                FakeExecution(),
                "run-1",
                item,
            )
        )

        self.assertEqual(["token", "done"], [event["event"] for event in events])
        done = events[-1]["data"]
        self.assertEqual("terminated", done["status"])
        self.assertTrue(done["auto_recovered"])
        self.assertFalse(done["failed_turn_saved"])
        self.assertEqual("parent_model_provider", done["failure_stage"])
        self.assertEqual("provider rejected this turn", done["message"])


if __name__ == "__main__":
    unittest.main()
