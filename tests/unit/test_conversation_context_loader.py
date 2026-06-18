import unittest
from pathlib import Path
from uuid import uuid4

from src.core.agent.models import RunLimits
from src.core.context.loader import ConversationContextLoader
from src.core.workspace.models import SessionContext, WorkspaceContext


class FakeStore:
    def __init__(self, completed_turn_index=0):
        self.completed_turn_index = completed_turn_index
        self.retrieve_calls = []
        self.memory_message_calls = []

    def load_session(self, _session):
        return "state", self.completed_turn_index

    def retrieve_for_turn(self, workspace_id, query, *, new_session):
        self.retrieve_calls.append((workspace_id, query, new_session))
        return ["memory-a"]

    def build_memory_message(self, memories):
        self.memory_message_calls.append(memories)
        return "memory-message" if memories else ""


class FakeContextManager:
    def __init__(self):
        self.calls = []

    def build_input_messages(self, state, user_input, *, extra_system_messages):
        self.calls.append((state, user_input, extra_system_messages))
        return ["input-message"]


class ConversationContextLoaderTest(unittest.TestCase):
    def _session(self):
        return SessionContext(
            session_id=uuid4(),
            session_name="default",
            workspace=WorkspaceContext(uuid4(), Path("workspace")),
        )

    def test_prepare_loads_memory_for_new_session_and_builds_input_messages(self):
        store = FakeStore(completed_turn_index=0)
        context_manager = FakeContextManager()
        session = self._session()

        prepared = ConversationContextLoader(context_manager).prepare(
            store=store,
            session=session,
            user_input="hello",
            run_id="run-1",
            limits=RunLimits(),
        )

        self.assertEqual(1, prepared.turn_index)
        self.assertEqual("run-1", prepared.run_context.run_id)
        self.assertEqual(["input-message"], prepared.input_messages)
        self.assertEqual(
            [(session.workspace.workspace_id, "hello", True)],
            store.retrieve_calls,
        )
        self.assertEqual([(["memory-a"])], store.memory_message_calls)
        self.assertEqual([("state", "hello", ["memory-message"])], context_manager.calls)

    def test_prepare_skips_memory_when_disabled(self):
        store = FakeStore(completed_turn_index=3)
        context_manager = FakeContextManager()
        session = self._session()

        prepared = ConversationContextLoader(
            context_manager,
            memory_enabled=False,
        ).prepare(
            store=store,
            session=session,
            user_input="hello",
            run_id="run-2",
            limits=RunLimits(),
        )

        self.assertEqual(4, prepared.turn_index)
        self.assertEqual([], store.retrieve_calls)
        self.assertEqual([([])], store.memory_message_calls)
        self.assertEqual([("state", "hello", [])], context_manager.calls)


if __name__ == "__main__":
    unittest.main()
