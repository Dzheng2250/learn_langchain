import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent_context import AgentContextState
from agent_hooks import AgentEvent, NoopEventSink, PostgresEventSink, set_event_sinks
from agent_memory import MemoryUnavailableError, PostgresMemoryStore
from tests.test_sql import (
    DELETE_TEST_EVENTS,
    DELETE_TEST_MEMORIES,
    DELETE_TEST_MESSAGES,
    DELETE_TEST_SESSION,
    SELECT_TEST_ARCHIVED_MESSAGES,
    SELECT_TEST_EVENT,
    UPSERT_TEST_MEMORY,
)


TEST_MARKER = "manual_memory_test"
TEST_SESSION_ID = "test_session_manual_memory_test"
TEST_SCOPE = "test_scope_manual_memory_test"
TEST_MEMORY_ID = "11111111-1111-1111-1111-111111111111"


class PostgresMemoryStoreManualTest(unittest.TestCase):
    """Manual database tests that can be run one by one.

    Run write/read/delete separately when you want to inspect database state
    between steps. These tests intentionally do not clean up automatically.
    """

    def setUp(self) -> None:
        set_event_sinks([NoopEventSink()])
        self.store = PostgresMemoryStore(retrieval_limit=5, min_importance=1)
        self.store.initialize()

    def tearDown(self) -> None:
        set_event_sinks(None)
        self.store.close()

    def test_01_write_test_data(self) -> None:
        """Write fixed test data and leave it in the database."""
        state = AgentContextState(
            summary=f"summary for {TEST_MARKER}",
            recent_messages=[
                HumanMessage(content=f"user message {TEST_MARKER}"),
                AIMessage(content=f"assistant message {TEST_MARKER}"),
            ],
        )
        self.store.save_session(TEST_SESSION_ID, state, turn_index=3)

        archived_ids = self.store.archive_turn_messages(
            TEST_SESSION_ID,
            turn_index=4,
            messages=[
                HumanMessage(content=f"archive user {TEST_MARKER}"),
                AIMessage(content=f"archive assistant {TEST_MARKER}"),
                ToolMessage(
                    content=f"archive tool {TEST_MARKER}",
                    tool_call_id=f"tool_call_{TEST_MARKER}",
                    name="smoke_tool",
                ),
            ],
        )

        memory_content = f"Project prefers PostgreSQL memory smoke marker {TEST_MARKER}"
        with self.store._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    UPSERT_TEST_MEMORY,
                    (
                        TEST_MEMORY_ID,
                        TEST_SCOPE,
                        "project_fact",
                        memory_content,
                        self.store._json_param(["smoke", "postgres"]),
                        5,
                        1.0,
                        self.store._json_param(archived_ids),
                    ),
                )
            conn.commit()

        print(f"\nWrote test session: {TEST_SESSION_ID}")
        print(f"Wrote test scope: {TEST_SCOPE}")
        print(f"Wrote test memory id: {TEST_MEMORY_ID}")
        self.assertEqual(3, len(archived_ids))

    def test_02_read_test_data(self) -> None:
        """Read fixed test data. Run test_01 first."""
        loaded_state, loaded_turn = self.store.load_session(TEST_SESSION_ID)
        self.assertEqual(3, loaded_turn)
        self.assertEqual(f"summary for {TEST_MARKER}", loaded_state.summary)
        self.assertEqual(2, len(loaded_state.recent_messages))
        self.assertEqual(f"user message {TEST_MARKER}", loaded_state.recent_messages[0].content)
        self.assertEqual(f"assistant message {TEST_MARKER}", loaded_state.recent_messages[1].content)

        with self.store._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(SELECT_TEST_ARCHIVED_MESSAGES, (TEST_SESSION_ID,))
                archived_rows = cur.fetchall()

        self.assertGreaterEqual(len(archived_rows), 3)
        self.assertIn(("user", "HumanMessage", f"archive user {TEST_MARKER}"), archived_rows)
        self.assertIn(("assistant", "AIMessage", f"archive assistant {TEST_MARKER}"), archived_rows)
        self.assertIn(("tool", "ToolMessage", f"archive tool {TEST_MARKER}"), archived_rows)

        memories = self.store.retrieve_memories(TEST_MARKER, scope=TEST_SCOPE)
        self.assertTrue(any(memory.id == TEST_MEMORY_ID for memory in memories))

        memory_message = self.store.build_memory_message(memories)
        self.assertIsNotNone(memory_message)
        self.assertIn(TEST_MARKER, memory_message.content)

        print(f"\nRead session turn: {loaded_turn}")
        print(f"Read archived rows: {len(archived_rows)}")
        print(f"Read memories: {len(memories)}")

    def test_03_delete_test_data(self) -> None:
        """Delete fixed test data and verify it is gone."""
        with self.store._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(DELETE_TEST_MESSAGES, (TEST_SESSION_ID,))
                deleted_messages = cur.rowcount

                cur.execute(DELETE_TEST_SESSION, (TEST_SESSION_ID,))
                deleted_sessions = cur.rowcount

                cur.execute(DELETE_TEST_MEMORIES, (TEST_MEMORY_ID, TEST_SCOPE, f"%{TEST_MARKER}%"))
                deleted_memories = cur.rowcount
            conn.commit()

        loaded_state, loaded_turn = self.store.load_session(TEST_SESSION_ID)
        memories = self.store.retrieve_memories(TEST_MARKER, scope=TEST_SCOPE)

        print(f"\nDeleted messages: {deleted_messages}")
        print(f"Deleted sessions: {deleted_sessions}")
        print(f"Deleted memories: {deleted_memories}")

        self.assertEqual(0, loaded_turn)
        self.assertEqual("", loaded_state.summary)
        self.assertEqual([], loaded_state.recent_messages)
        self.assertFalse(any(memory.id == TEST_MEMORY_ID for memory in memories))

    def test_04_write_and_delete_event_data(self) -> None:
        """Write one fixed event and delete it."""
        run_id = "test_run_manual_memory_test"
        sink = PostgresEventSink(async_write=True, batch_size=2, flush_interval_seconds=0.1)
        sink.emit(
            AgentEvent(
                event_type="test_event",
                source="test_memory_store",
                message="test event write",
                payload={"marker": TEST_MARKER},
                session_id=TEST_SESSION_ID,
                turn_index=99,
                run_id=run_id,
            )
        )
        sink.flush()
        sink.close()

        with self.store._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(SELECT_TEST_EVENT, (run_id,))
                row = cur.fetchone()

                cur.execute(DELETE_TEST_EVENTS, (run_id,))
                deleted_events = cur.rowcount
            conn.commit()

        self.assertIsNotNone(row)
        self.assertEqual("test_event", row[0])
        self.assertEqual("test_memory_store", row[1])
        self.assertEqual("test event write", row[2])
        self.assertEqual(TEST_MARKER, row[3]["marker"])
        self.assertEqual(1, deleted_events)


if __name__ == "__main__":
    try:
        PostgresMemoryStore().initialize()
        unittest.main(verbosity=2)
    except MemoryUnavailableError as exc:
        raise SystemExit(f"Memory database unavailable: {exc}") from exc
