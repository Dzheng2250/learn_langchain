import unittest

from langchain_core.messages import AIMessage, HumanMessage

from src.core.context.models import AgentContextState
from src.core.finalization.committer import CompletedTurnCommitter
from src.core.finalization.models import CompletedTurn


class FakeHistory:
    def __init__(self, calls):
        self.calls = calls

    def append_turn(self, completed):
        self.calls.append(("history", completed.turn_index))
        return ["message-1", "message-2"]


class FakeSessions:
    def __init__(self, calls):
        self.calls = calls

    def save_fast_context(self, completed):
        self.calls.append(("session", completed.turn_index))


class FakeExecutions:
    def __init__(self, calls):
        self.calls = calls

    def finish_completed_turn(self, completed):
        self.calls.append(("execution", completed.execution_id))


class FakeMaintenance:
    def __init__(self, calls):
        self.calls = calls

    def enqueue(self, spec):
        self.calls.append(("maintenance", spec))
        return "job"


class FakeUnitOfWork:
    def __init__(self):
        self.calls = []
        self.history = FakeHistory(self.calls)
        self.sessions = FakeSessions(self.calls)
        self.executions = FakeExecutions(self.calls)
        self.maintenance = FakeMaintenance(self.calls)
        self.committed = False

    def __enter__(self):
        self.calls.append(("enter", None))
        return self

    def __exit__(self, exc_type, exc, tb):
        self.calls.append(("exit", exc_type))

    def commit(self):
        self.committed = True
        self.calls.append(("commit", None))

    def rollback(self):
        self.calls.append(("rollback", None))


class FakeUnitOfWorkFactory:
    def __init__(self):
        self.last = None

    def begin(self, _store):
        self.last = FakeUnitOfWork()
        return self.last


class CompletedTurnCommitterPortTest(unittest.TestCase):
    def test_committer_depends_on_unit_of_work_port(self):
        factory = FakeUnitOfWorkFactory()
        committer = CompletedTurnCommitter(factory)
        completed = CompletedTurn(
            session=object(),
            turn_index=1,
            messages=[HumanMessage(content="hi"), AIMessage(content="ok")],
            state=AgentContextState(),
            execution_id="execution-1",
            jobs=("job-spec",),
        )

        message_ids = committer.commit(object(), completed)

        self.assertEqual(["message-1", "message-2"], message_ids)
        self.assertTrue(factory.last.committed)
        self.assertEqual(
            [
                ("enter", None),
                ("history", 1),
                ("session", 1),
                ("execution", "execution-1"),
                ("maintenance", "job-spec"),
                ("commit", None),
                ("exit", None),
            ],
            factory.last.calls,
        )


if __name__ == "__main__":
    unittest.main()
