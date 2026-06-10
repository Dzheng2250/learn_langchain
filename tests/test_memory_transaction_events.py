import unittest

from src.core.hooks.events import set_event_sinks
from src.core.memory.store import PostgresMemoryStore


class MemorySink:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)


class FakeExtractor:
    def format_messages(self, messages: list) -> str:
        return "source"

    def extract(self, source: str) -> list[dict]:
        return [
            {
                "kind": "project_fact",
                "content": "stable project fact",
                "importance": 5,
                "confidence": 1.0,
                "tags": ["test"],
            }
        ]

    def looks_sensitive(self, content: str) -> bool:
        return False


class FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, query, params) -> None:
        return

    def fetchone(self):
        return None


class FakeConnection:
    def __init__(self, fail_commit: bool) -> None:
        self.fail_commit = fail_commit

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def cursor(self) -> FakeCursor:
        return FakeCursor()

    def commit(self) -> None:
        if self.fail_commit:
            raise RuntimeError("commit failed")


class MemoryTransactionEventsTest(unittest.TestCase):
    def tearDown(self) -> None:
        set_event_sinks(None)

    def _store(self, fail_commit: bool) -> PostgresMemoryStore:
        store = PostgresMemoryStore.__new__(PostgresMemoryStore)
        store.min_importance = 1
        store.extractor = FakeExtractor()
        store._Jsonb = lambda value, dumps=None: value
        store._connect = lambda: FakeConnection(fail_commit)
        return store

    def test_memory_saved_is_emitted_after_successful_commit(self) -> None:
        sink = MemorySink()
        set_event_sinks([sink])

        self._store(fail_commit=False).extract_and_save_memories("session", 1, [object()], [1])

        event_types = [event.event_type for event in sink.events]
        self.assertIn("memory_saved", event_types)
        self.assertNotIn("memory_failed", event_types)

    def test_commit_failure_does_not_emit_memory_saved(self) -> None:
        sink = MemorySink()
        set_event_sinks([sink])

        with self.assertRaisesRegex(RuntimeError, "commit failed"):
            self._store(fail_commit=True).extract_and_save_memories("session", 1, [object()], [1])

        event_types = [event.event_type for event in sink.events]
        self.assertNotIn("memory_saved", event_types)
        self.assertIn("memory_failed", event_types)


if __name__ == "__main__":
    unittest.main(verbosity=2)
