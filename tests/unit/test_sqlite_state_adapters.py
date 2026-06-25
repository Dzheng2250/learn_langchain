import unittest
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from src.config.settings import RECENT_TURN_LIMIT

from src.core.adapters.sqlite import (
    SQLiteConversationHistoryStore,
    SQLiteMemoryRetrievalStore,
    SQLiteMemoryWriteStore,
    SQLiteProjectionOutboxStore,
    SQLiteSessionStore,
    SQLiteSummaryStore,
)
from src.core.adapters.sqlite.session_lifecycle import SQLiteSessionLifecycleStore
from src.core.context.models import AgentContextState, TurnChunk
from src.core.finalization.models import CompletedTurn
from src.core.state import LocalStateDatabase, LocalStateStore
from src.core.state.workspace import LocalWorkspaceRepository
from src.core.telemetry import BaseEventSink, EventBus, install_event_bus
from tests.support.model_providers import UnusedModelProvider


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
        self.workspace_repository = LocalWorkspaceRepository(self.database)
        self.workspace = self.workspace_repository.resolve(
            str(Path("tests/fixtures/workspace_a").resolve())
        )
        self.session, _ = self.workspace_repository.resolve_session(
            self.workspace,
            "default",
        )
        self.store = LocalStateStore(self.database, UnusedModelProvider())

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


    def test_conversation_history_content_projection_extracts_text_blocks(self):
        self.archive_and_save(
            1,
            [AIMessage(content=[{"type": "text", "text": "block answer"}])],
            AgentContextState(),
        )

        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT content, raw FROM messages WHERE session_id=?",
                (str(self.session.session_id),),
            ).fetchone()

        self.assertEqual("block answer", row["content"])
        self.assertIn("block answer", row["raw"])
    def test_conversation_append_turn_requires_unit_of_work_transaction(self):
        completed = CompletedTurn(
            session=self.session,
            turn_index=1,
            messages=[HumanMessage(content="outside uow")],
            state=AgentContextState(),
        )

        with self.assertRaisesRegex(RuntimeError, "active Unit of Work"):
            SQLiteConversationHistoryStore(self.database).append_turn(completed)


    def test_message_ordinal_is_session_local_and_unique(self):
        other_session, _ = self.workspace_repository.resolve_session(
            self.workspace,
            "other",
        )
        with self.database.transaction() as conn:
            SQLiteConversationHistoryStore(
                self.database,
                transaction_conn=conn,
            ).append_messages(
                self.session,
                1,
                [HumanMessage(content="default-1"), AIMessage(content="default-2")],
            )
            SQLiteConversationHistoryStore(
                self.database,
                transaction_conn=conn,
            ).append_messages(
                other_session,
                1,
                [HumanMessage(content="other-1"), AIMessage(content="other-2")],
            )

        with self.database.connect() as conn:
            default_ordinals = [
                row["message_ordinal"]
                for row in conn.execute(
                    """
                    SELECT message_ordinal FROM messages
                    WHERE session_id=? ORDER BY message_ordinal
                    """,
                    (str(self.session.session_id),),
                ).fetchall()
            ]
            other_ordinals = [
                row["message_ordinal"]
                for row in conn.execute(
                    """
                    SELECT message_ordinal FROM messages
                    WHERE session_id=? ORDER BY message_ordinal
                    """,
                    (str(other_session.session_id),),
                ).fetchall()
            ]

        self.assertEqual([1, 2], default_ordinals)
        self.assertEqual([1, 2], other_ordinals)
        with self.assertRaises(Exception):
            with self.database.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO messages(
                        message_id, workspace_id, session_id, role, message_type,
                        content, raw, turn_index, message_ordinal
                    ) VALUES ('duplicate-ordinal', ?, ?, 'user', 'HumanMessage',
                              'duplicate', '{}', 99, 1)
                    """,
                    (
                        str(self.workspace.workspace_id),
                        str(self.session.session_id),
                    ),
                )
    def test_message_order_uses_explicit_ordinal_not_created_at_or_id(self):
        self.archive_and_save(
            1,
            [
                HumanMessage(content="first"),
                AIMessage(content="second"),
                HumanMessage(content="third"),
            ],
            AgentContextState(),
        )
        with self.database.transaction() as conn:
            conn.execute(
                """
                UPDATE messages SET created_at='2099-01-01 00:00:00'
                WHERE content='first'
                """
            )
            conn.execute(
                """
                UPDATE messages SET created_at='2000-01-01 00:00:00'
                WHERE content='third'
                """
            )

        messages, _message_ids = SQLiteConversationHistoryStore(self.database).load_turn(
            self.session,
            1,
        )

        self.assertEqual(["first", "second", "third"], [message.content for message in messages])
    def test_conversation_history_rebuild_recent_uses_latest_committed_turns(self):
        total_turns = 15
        messages_per_turn = 2
        expected_turns = min(RECENT_TURN_LIMIT, total_turns)
        expected_messages = expected_turns * messages_per_turn
        expected_first_turn = total_turns - expected_turns + 1
        for index in range(total_turns):
            self.store.archive_turn_messages(
                self.session,
                index + 1,
                [
                    HumanMessage(content=f"user-{index}"),
                    AIMessage(content=f"answer-{index}"),
                ],
            )
        with self.database.transaction() as conn:
            conn.execute(
                "UPDATE sessions SET recent_messages='[]', context_tokens=99 WHERE session_id=?",
                (str(self.session.session_id),),
            )

        recovered = SQLiteConversationHistoryStore(self.database).rebuild_recent(self.session)
        state, _turn_index = self.store.load_session(self.session)

        self.assertEqual(expected_messages, recovered)
        self.assertEqual(expected_turns, len(state.recent_turns))
        self.assertEqual(expected_messages, len(state.recent_messages))
        self.assertEqual(expected_first_turn, state.recent_turns[0].turn_index)
        self.assertEqual(
            f"user-{expected_first_turn - 1}", state.recent_messages[0].content
        )
        self.assertEqual("answer-14", state.recent_messages[-1].content)
        self.assertEqual(0, state.context_tokens)


    def test_session_store_loads_legacy_recent_message_cache(self):
        legacy = AgentContextState(recent_messages=[HumanMessage(content="legacy")])
        self.store.save_session(self.session, legacy, 1)
        with self.database.transaction() as conn:
            conn.execute(
                "UPDATE sessions SET recent_messages=? WHERE session_id=?",
                (
                    '[{"type":"human","data":{"content":"legacy flat","additional_kwargs":{},"response_metadata":{},"type":"human","name":null,"id":null}}]',
                    str(self.session.session_id),
                ),
            )

        state, _turn_index = SQLiteSessionStore(self.database).load_context(self.session)

        self.assertEqual([0], [turn.turn_index for turn in state.recent_turns])
        self.assertEqual(["legacy flat"], [message.content for message in state.recent_messages])

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


    def test_new_session_has_active_context_window(self):
        with self.database.connect() as conn:
            session = conn.execute(
                """
                SELECT active_context_window_id FROM sessions WHERE session_id=?
                """,
                (str(self.session.session_id),),
            ).fetchone()
            window = conn.execute(
                """
                SELECT window_id, first_window_id, previous_window_id,
                       summary_text, summary_through_turn, opened_at_turn
                FROM context_windows WHERE window_id=?
                """,
                (session["active_context_window_id"],),
            ).fetchone()

        self.assertEqual(f"root-{self.session.session_id}", session["active_context_window_id"])
        self.assertEqual(window["window_id"], window["first_window_id"])
        self.assertIsNone(window["previous_window_id"])
        self.assertEqual("", window["summary_text"])
        self.assertEqual(0, window["summary_through_turn"])
        self.assertEqual(0, window["opened_at_turn"])

    def test_session_store_prefers_active_context_window_summary(self):
        adapter = SQLiteSummaryStore(self.database)
        self.assertTrue(
            adapter.update_summary_cas(
                self.session,
                expected_summary_through_turn=0,
                summary_through_turn=1,
                summary="window summary",
            )
        )
        with self.database.transaction() as conn:
            conn.execute(
                "UPDATE sessions SET summary='stale compatibility summary' WHERE session_id=?",
                (str(self.session.session_id),),
            )

        state, _turn_index = SQLiteSessionStore(self.database).load_context(self.session)

        self.assertEqual("window summary", state.summary)

    def test_summary_store_skips_empty_compaction_window(self):
        adapter = SQLiteSummaryStore(self.database)

        changed = adapter.update_summary_cas(
            self.session,
            expected_summary_through_turn=0,
            summary_through_turn=0,
            summary="same watermark",
        )

        with self.database.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM context_windows WHERE session_id=?",
                (str(self.session.session_id),),
            ).fetchone()["count"]
            summary = conn.execute(
                "SELECT summary FROM sessions WHERE session_id=?",
                (str(self.session.session_id),),
            ).fetchone()["summary"]

        self.assertTrue(changed)
        self.assertEqual(1, count)
        self.assertEqual("", summary)

    def test_summary_store_repairs_missing_active_window_with_event(self):
        sink = RecordingSink()
        install_event_bus(EventBus([sink]))
        with self.database.transaction() as conn:
            conn.execute("DELETE FROM context_windows WHERE session_id=?", (str(self.session.session_id),))
            conn.execute(
                "UPDATE sessions SET active_context_window_id=NULL, summary='legacy summary' WHERE session_id=?",
                (str(self.session.session_id),),
            )

        summary, watermark, messages = SQLiteSummaryStore(self.database).load_summary_source(
            self.session,
            0,
        )

        with self.database.connect() as conn:
            active = conn.execute(
                "SELECT active_context_window_id FROM sessions WHERE session_id=?",
                (str(self.session.session_id),),
            ).fetchone()["active_context_window_id"]

        self.assertEqual("legacy summary", summary)
        self.assertEqual(0, watermark)
        self.assertEqual([], messages)
        self.assertEqual(f"root-{self.session.session_id}", active)
        self.assertIn("context_window_repaired", [event.event_type for event in sink.events])

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

        with self.database.connect() as conn:
            root = conn.execute(
                """
                SELECT window_id, closed_at_turn FROM context_windows
                WHERE session_id=? AND previous_window_id IS NULL
                """,
                (str(self.session.session_id),),
            ).fetchone()
            active = conn.execute(
                "SELECT active_context_window_id FROM sessions WHERE session_id=?",
                (str(self.session.session_id),),
            ).fetchone()["active_context_window_id"]
            active_window = conn.execute(
                """
                SELECT previous_window_id, summary_text, summary_through_turn
                FROM context_windows WHERE window_id=?
                """,
                (active,),
            ).fetchone()
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM context_windows WHERE session_id=?",
                (str(self.session.session_id),),
            ).fetchone()["count"]

        self.assertEqual(2, count)
        self.assertEqual(f"root-{self.session.session_id}", root["window_id"])
        self.assertEqual(root["window_id"], active_window["previous_window_id"])
        self.assertEqual("fresh", active_window["summary_text"])
        self.assertEqual(2, active_window["summary_through_turn"])
        self.assertEqual(2, root["closed_at_turn"])

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

    def test_session_lifecycle_store_preserves_archive_semantics(self):
        adapter = SQLiteSessionLifecycleStore(
            workspace_repository=self.workspace_repository,
            history_store=SQLiteConversationHistoryStore(self.database),
        )

        active = adapter.find_session(self.workspace, "default")
        archived_now = adapter.archive(self.session)
        archived = adapter.find_session(self.workspace, "default")

        self.assertFalse(active[1])
        self.assertTrue(archived_now)
        self.assertTrue(archived[1])

    def test_session_lifecycle_store_rebuilds_recent_history(self):
        self.archive_and_save(
            1,
            [HumanMessage(content="recover me")],
            AgentContextState(),
        )
        adapter = SQLiteSessionLifecycleStore(
            workspace_repository=self.workspace_repository,
            history_store=SQLiteConversationHistoryStore(self.database),
        )

        count = adapter.rebuild_recent(self.session)
        state, turn_index = SQLiteSessionStore(self.database).load_context(self.session)

        self.assertEqual(1, count)
        self.assertEqual(1, turn_index)
        self.assertEqual(["recover me"], [message.content for message in state.recent_messages])


if __name__ == "__main__":
    unittest.main()
