import unittest

from src.core.execution import ExecutionLifecycleService
from src.core.state.types import ExecutionStatus


class FakeExecution:
    def __init__(
        self,
        execution_id="exec-1",
        status=ExecutionStatus.RUNNING,
        goal_mode=False,
    ):
        self.execution_id = execution_id
        self.status = status
        self.goal_mode = goal_mode


class FakeRepository:
    def __init__(self):
        self.pending = None
        self.attached = None
        self.begun = []
        self.resumed = []
        self.paused = []

    def get_pending(self, session):
        return self.pending

    def get_attached(self, session):
        return self.attached

    def begin(self, session, user_input, *, goal_mode=False):
        execution = FakeExecution("new-exec", goal_mode=goal_mode)
        self.begun.append((session, user_input, goal_mode))
        return execution

    def resume(self, session, *, resume_value=None, retry_conditions=False):
        execution = FakeExecution("resumed-exec", goal_mode=True)
        self.resumed.append((session, resume_value, retry_conditions))
        return execution

    def pause(self, execution_id, status, stop_reason, summary):
        self.paused.append((execution_id, status, stop_reason, summary))


class ExecutionLifecycleServiceTest(unittest.TestCase):
    def test_begin_turn_returns_existing_pending_without_creating_new_execution(self):
        repository = FakeRepository()
        repository.pending = FakeExecution("pending-exec")

        result = ExecutionLifecycleService(repository).begin_turn(
            "session",
            "goal",
            goal_mode=True,
        )

        self.assertTrue(result.blocked_by_pending)
        self.assertEqual("pending-exec", result.pending.execution_id)
        self.assertEqual([], repository.begun)

    def test_begin_turn_creates_execution_when_session_is_idle(self):
        repository = FakeRepository()

        result = ExecutionLifecycleService(repository).begin_turn(
            "session",
            "goal",
            goal_mode=True,
        )

        self.assertFalse(result.blocked_by_pending)
        self.assertEqual("new-exec", result.execution.execution_id)
        self.assertEqual([("session", "goal", True)], repository.begun)

    def test_resume_delegates_to_repository(self):
        repository = FakeRepository()

        pending = ExecutionLifecycleService(repository).resume("session")

        self.assertEqual("resumed-exec", pending.execution_id)
        self.assertEqual([("session", None, False)], repository.resumed)

    def test_runtime_creation_failure_pauses_when_execution_exists(self):
        repository = FakeRepository()
        service = ExecutionLifecycleService(repository)

        service.pause_runtime_creation_failed(FakeExecution("exec-2"), RuntimeError("boom"))

        self.assertEqual("exec-2", repository.paused[0][0])
        self.assertEqual("turn_error", repository.paused[0][2])
        self.assertIn("Workspace runtime creation failed", repository.paused[0][3])

    def test_execution_store_is_a_required_dependency(self):
        with self.assertRaises(TypeError):
            ExecutionLifecycleService()


if __name__ == "__main__":
    unittest.main()
