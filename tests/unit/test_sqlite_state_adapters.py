import unittest
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from src.core.adapters.sqlite import (
    SQLiteConversationHistoryStore,
    SQLiteMemoryRetrievalStore,
    SQLiteMemoryWriteStore,
    SQLiteProjectionOutboxStore,
    SQLiteSessionStore,
    SQLiteSummaryStore,
)
from src.core.context.models import AgentContextState
from src.core.finalization.models import CompletedTurn
from src.core.state import LocalStateDatabase, LocalStateStore
from src.core.state.workspace import LocalWorkspaceRepository
from src.core.telemetry import BaseEventSink, EventBus, install_event_bus


class RecordingSink(BaseEventSink):
    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)


class FakeMemoryExtractor:
    def __init__(self, candidates) -> None:
        self.candidates = candidates

    def format_messages(self, messages: list) -> str:
        return "source"

    def extract(self, source: str) -> list[dict]:
        return self.candidates

    def looks_sensitive(self, content: str) -> bool:
        return False


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

    def tearDown(self):
        install_event_bus(None)

    def archive_and_save(self, turn_index: int, messages: list, state: AgentContextState) -> None:
        with self.database.transaction() as conn:
            SQLiteConversationHistoryStore(
                self.database,
                transaction_conn=conn,
            ).append_messages(self.session, turn_index, messages)
            SQLiteSessionStore(
                self.database,
                transaction_conn=conn,
            ).save_context(self.session, state, turn_index)

    def test_conversation_history_load_turn_preserves_message_order_and_ids(self):
        self.archive_and_save(
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

    def test_conversation_append_turn_requires_unit_of_work_transaction(self):
        completed = CompletedTurn(
            session=self.session,
            turn_index=1,
            messages=[HumanMessage(content="outside uow")],
            state=AgentContextState(),
        )

        with self.assertRaisesRegex(RuntimeError, "active Unit of Work"):
            SQLiteConversationHistoryStore(self.database).append_turn(completed)

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

    def test_session_store_save_context_updates_summary_and_turn_index(self):
        adapter = SQLiteSessionStore(self.database)
        expected = AgentContextState(
            summary="adapter summary",
            recent_messages=[HumanMessage(content="adapter recent")],
            context_tokens=321,
        )

        with self.database.transaction() as conn:
            SQLiteSessionStore(self.database, transaction_conn=conn).save_context(
                self.session,
                expected,
                9,
            )

        state, turn_index = adapter.load_context(self.session)
        self.assertEqual("adapter summary", state.summary)
        self.assertEqual(["adapter recent"], [message.content for message in state.recent_messages])
        self.assertEqual(321, state.context_tokens)
        self.assertEqual(9, turn_index)

    def test_session_store_fast_context_does_not_overwrite_summary(self):
        self.store.save_session(
            self.session,
            AgentContextState(summary="kept summary"),
            1,
        )

        with self.database.transaction() as conn:
            SQLiteSessionStore(self.database, transaction_conn=conn).save_fast_context_values(
                self.session,
                AgentContextState(
                    summary="ignored summary",
                    recent_messages=[HumanMessage(content="fast recent")],
                    context_tokens=456,
                ),
                10,
            )

        state, turn_index = SQLiteSessionStore(self.database).load_context(self.session)
        self.assertEqual("kept summary", state.summary)
        self.assertEqual(["fast recent"], [message.content for message in state.recent_messages])
        self.assertEqual(456, state.context_tokens)
        self.assertEqual(10, turn_index)

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

    def test_summary_store_loads_only_unsummarized_messages(self):
        self.archive_and_save(
            1,
            [HumanMessage(content="old")],
            AgentContextState(),
        )
        self.archive_and_save(
            2,
            [HumanMessage(content="new")],
            AgentContextState(),
        )
        adapter = SQLiteSummaryStore(self.database)
        self.assertTrue(
            adapter.update_summary_cas(
                self.session,
                expected_summary_through_turn=0,
                summary_through_turn=1,
                summary="summary-1",
            )
        )

        summary, watermark, messages = adapter.load_summary_source(self.session, 2)

        self.assertEqual("summary-1", summary)
        self.assertEqual(1, watermark)
        self.assertEqual([(2, "new")], [(turn, msg.content) for turn, msg in messages])

    def test_summary_store_cas_does_not_overwrite_newer_summary(self):
        adapter = SQLiteSummaryStore(self.database)

        self.assertTrue(
            adapter.update_summary_cas(
                self.session,
                expected_summary_through_turn=0,
                summary_through_turn=2,
                summary="fresh",
            )
        )
        self.assertFalse(
            adapter.update_summary_cas(
                self.session,
                expected_summary_through_turn=0,
                summary_through_turn=1,
                summary="stale",
            )
        )
        summary, watermark, _messages = adapter.load_summary_source(self.session, 3)

        self.assertEqual("fresh", summary)
        self.assertEqual(2, watermark)

    def test_memory_write_store_saves_sources_and_emits_after_commit(self):
        sink = RecordingSink()
        install_event_bus(EventBus([sink]))
        message_ids = self.store.archive_turn_messages(
            self.session,
            1,
            [HumanMessage(content="remember this")],
        )
        adapter = SQLiteMemoryWriteStore(
            self.database,
            extractor=FakeMemoryExtractor(
                [
                    {
                        "kind": "project_fact",
                        "content": "adapter-owned memory",
                        "importance": 5,
                        "confidence": 0.9,
                        "tags": ["adapter"],
                    }
                ]
            ),
            min_importance=1,
            projection_enabled=True,
        )

        saved = adapter.extract_and_save(
            self.session,
            1,
            [HumanMessage(content="remember this")],
            message_ids,
        )

        with self.database.connect() as conn:
            memory = conn.execute(
                "SELECT memory_id, content FROM memories WHERE workspace_id=?",
                (str(self.workspace.workspace_id),),
            ).fetchone()
            source_count = conn.execute(
                "SELECT count(*) AS count FROM memory_sources WHERE memory_id=?",
                (memory["memory_id"],),
            ).fetchone()["count"]
            outbox = conn.execute(
                "SELECT event_type FROM projection_outbox WHERE aggregate_id=?",
                (memory["memory_id"],),
            ).fetchone()

        self.assertIn("adapter-owned memory", saved[0])
        self.assertEqual("adapter-owned memory", memory["content"])
        self.assertEqual(len(message_ids), source_count)
        self.assertEqual("memory_saved", outbox["event_type"])
        self.assertIn("memory_saved", [event.event_type for event in sink.events])

    def test_projection_outbox_store_respects_enabled_flag(self):
        with self.database.transaction() as conn:
            SQLiteProjectionOutboxStore(
                self.database,
                transaction_conn=conn,
                enabled=False,
            ).enqueue("turn_committed", "session", "session-1", {"turn_index": 1})
            SQLiteProjectionOutboxStore(
                self.database,
                transaction_conn=conn,
                enabled=True,
            ).enqueue("turn_committed", "session", "session-2", {"turn_index": 2})

        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT aggregate_id, payload FROM projection_outbox ORDER BY aggregate_id"
            ).fetchall()

        self.assertEqual(1, len(rows))
        self.assertEqual("session-2", rows[0]["aggregate_id"])
        self.assertIn('"turn_index": 2', rows[0]["payload"])


if __name__ == "__main__":
    unittest.main()
