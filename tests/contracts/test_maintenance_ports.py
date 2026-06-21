import unittest
from pathlib import Path

from langchain_core.messages import HumanMessage

from src.core.adapters.sqlite import (
    SQLiteConversationHistoryStore,
    SQLiteMemoryWriteStore,
    SQLiteSummaryStore,
)
from src.core.state import CheckpointManager, LocalStateDatabase, LocalWorkspaceRepository


class FakeMemoryExtractor:
    def format_messages(self, messages: list) -> str:
        return "\n".join(str(message.content) for message in messages)

    def extract(self, _source: str) -> list[dict]:
        return [
            {
                "kind": "project_fact",
                "content": "The project uses interface ports.",
                "importance": 5,
                "confidence": 1.0,
                "tags": ["architecture"],
            }
        ]

    def looks_sensitive(self, _content: str) -> bool:
        return False


class SQLiteMaintenancePortContractTest(unittest.TestCase):
    def setUp(self):
        self.database = LocalStateDatabase(":memory:")
        self.addCleanup(self.database.close)
        self.database.initialize()
        workspaces = LocalWorkspaceRepository(self.database)
        workspace = workspaces.resolve(
            str(Path("tests/fixtures/workspace_a").resolve())
        )
        self.session, _ = workspaces.resolve_session(workspace, "maintenance-contract")

    def append_turn(self, turn_index: int, content: str) -> list[str]:
        with self.database.transaction() as conn:
            return SQLiteConversationHistoryStore(
                self.database,
                transaction_conn=conn,
            ).append_messages(
                self.session,
                turn_index,
                [HumanMessage(content=content)],
            )

    def test_summary_store_honors_compare_and_swap_watermark(self):
        self.append_turn(1, "first")
        store = SQLiteSummaryStore(self.database)

        previous, watermark, indexed = store.load_summary_source(self.session, 1)

        self.assertEqual("", previous)
        self.assertEqual(0, watermark)
        self.assertEqual([(1, "first")], [(turn, msg.content) for turn, msg in indexed])
        self.assertTrue(
            store.update_summary_cas(
                self.session,
                expected_summary_through_turn=0,
                summary_through_turn=1,
                summary="summary",
            )
        )
        self.assertFalse(
            store.update_summary_cas(
                self.session,
                expected_summary_through_turn=0,
                summary_through_turn=1,
                summary="stale",
            )
        )

    def test_memory_writer_preserves_source_message_relationship(self):
        source_ids = self.append_turn(1, "remember the architecture")
        messages, loaded_ids = SQLiteConversationHistoryStore(
            self.database
        ).load_turn(self.session, 1)
        store = SQLiteMemoryWriteStore(
            self.database,
            extractor=FakeMemoryExtractor(),
            min_importance=1,
            projection_enabled=False,
        )

        saved = store.extract_and_save(
            self.session,
            1,
            messages,
            loaded_ids,
        )

        self.assertEqual(source_ids, loaded_ids)
        self.assertEqual(1, len(saved))
        with self.database.connect() as conn:
            memory_count = conn.execute("SELECT count(*) FROM memories").fetchone()[0]
            source_count = conn.execute(
                "SELECT count(*) FROM memory_sources"
            ).fetchone()[0]
        self.assertEqual(1, memory_count)
        self.assertEqual(1, source_count)


class CheckpointStoreContractTest(unittest.TestCase):
    def test_checkpoint_lifecycle_is_idempotent_and_rejects_use_after_close(self):
        manager = CheckpointManager(":memory:")
        self.addCleanup(manager.close)

        first = manager.initialize()
        second = manager.initialize()

        self.assertIs(first, second)
        self.assertFalse(manager.thread_exists("missing-thread"))
        manager.delete_thread("missing-thread")
        self.assertFalse(manager.thread_exists("missing-thread"))
        manager.close()
        with self.assertRaisesRegex(RuntimeError, "must be initialized"):
            manager.thread_exists("missing-thread")


if __name__ == "__main__":
    unittest.main()
