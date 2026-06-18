import unittest
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from src.core.adapters.sqlite import (
    SQLiteConversationHistoryStore,
    SQLiteMemoryRetrievalStore,
    SQLiteSessionStore,
)
from src.core.context.models import AgentContextState
from src.core.state import LocalStateDatabase, LocalStateStore
from src.core.state.workspace import LocalWorkspaceRepository


class SQLiteStateAdapterTest(unittest.TestCase):
    def setUp(self):
        self.database = LocalStateDatabase(":memory:")
        self.addCleanup(self.database.close)
        self.database.initialize()
        workspace_repository = LocalWorkspaceRepository(self.database)
        self.workspace = workspace_repository.resolve(
            str(Path("tests/fixtures/workspace_a").resolve())
        )
        self.session, _ = workspace_repository.resolve_session(self.workspace, "default")
        self.store = LocalStateStore(self.database)

    def test_conversation_history_load_turn_preserves_message_order_and_ids(self):
        self.store.commit_turn(
            self.session,
            1,
            [
                HumanMessage(content="first"),
                AIMessage(content="second"),
                HumanMessage(content="third"),
            ],
            AgentContextState(),
        )

        messages, message_ids = SQLiteConversationHistoryStore(self.database).load_turn(
            self.session,
            1,
        )

        self.assertEqual(["first", "second", "third"], [message.content for message in messages])
        self.assertEqual(3, len(message_ids))
        self.assertEqual(len(set(message_ids)), len(message_ids))

    def test_conversation_history_rebuild_recent_uses_latest_committed_messages(self):
        for index in range(15):
            self.store.archive_turn_messages(
                self.session,
                index + 1,
                [HumanMessage(content=f"message-{index}")],
            )
        with self.database.transaction() as conn:
            conn.execute(
                "UPDATE sessions SET recent_messages='[]', context_tokens=99 WHERE session_id=?",
                (str(self.session.session_id),),
            )

        recovered = SQLiteConversationHistoryStore(self.database).rebuild_recent(self.session)
        state, _turn_index = self.store.load_session(self.session)

        self.assertEqual(12, recovered)
        self.assertEqual(12, len(state.recent_messages))
        self.assertEqual("message-3", state.recent_messages[0].content)
        self.assertEqual("message-14", state.recent_messages[-1].content)
        self.assertEqual(0, state.context_tokens)

    def test_session_store_load_context_matches_session_state(self):
        expected = AgentContextState(
            summary="summary",
            recent_messages=[HumanMessage(content="recent")],
            context_tokens=123,
        )
        self.store.save_session(self.session, expected, 7)

        state, turn_index = SQLiteSessionStore(self.database).load_context(self.session)

        self.assertEqual("summary", state.summary)
        self.assertEqual(["recent"], [message.content for message in state.recent_messages])
        self.assertEqual(123, state.context_tokens)
        self.assertEqual(7, turn_index)

    def test_memory_retrieval_store_keeps_bootstrap_relevant_limits_and_dedupe(self):
        with self.database.transaction() as conn:
            for memory_id, content, importance in (
                ("memory-a", "alpha project rule", 9),
                ("memory-b", "beta project note", 8),
                ("memory-c", "unrelated memory", 7),
            ):
                conn.execute(
                    """
                    INSERT INTO memories(memory_id, workspace_id, kind, content, tags, importance, confidence)
                    VALUES (?, ?, 'project_fact', ?, '[]', ?, 0.9)
                    """,
                    (
                        memory_id,
                        str(self.workspace.workspace_id),
                        content,
                        importance,
                    ),
                )

        adapter = SQLiteMemoryRetrievalStore(self.database, retrieval_limit=2)
        memories = adapter.retrieve_for_turn(
            self.workspace.workspace_id,
            "alpha",
            new_session=True,
        )

        self.assertEqual(["memory-a", "memory-b"], [memory.id for memory in memories])
        self.assertIn("alpha project rule", adapter.build_memory_message(memories).content)


if __name__ == "__main__":
    unittest.main()
