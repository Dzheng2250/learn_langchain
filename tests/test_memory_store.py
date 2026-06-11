import unittest
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from psycopg.errors import ForeignKeyViolation

from src.core.context.models import AgentContextState
from src.core.database.connection import create_pool
from src.core.hooks.events import NoopEventSink, set_event_sinks
from src.core.hooks.models import AgentEvent
from src.core.hooks.sinks import PostgresEventSink
from src.core.memory.store import PostgresMemoryStore
from src.core.workspace.repository import WorkspaceRepository


class WorkspaceMemoryStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        set_event_sinks([NoopEventSink()])
        self.pool = create_pool()
        self.addCleanup(self.pool.close)
        self.store = PostgresMemoryStore(pool=self.pool, retrieval_limit=5, min_importance=1)
        self.store.initialize()
        repository = WorkspaceRepository(self.pool)
        self.workspace_a = repository.resolve(Path("tests/fixtures/workspace_a"))
        self.workspace_b = repository.resolve(Path("tests/fixtures/workspace_b"))
        self.session_a, _ = repository.resolve_session(self.workspace_a, "default")
        self.session_b, _ = repository.resolve_session(self.workspace_b, "default")

    def tearDown(self) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM agent_sessions WHERE workspace_id = ANY(%s)",
                    ([self.workspace_a.workspace_id, self.workspace_b.workspace_id],),
                )
                cur.execute(
                    "DELETE FROM agent_workspaces WHERE workspace_id = ANY(%s)",
                    ([self.workspace_a.workspace_id, self.workspace_b.workspace_id],),
                )
            conn.commit()
        set_event_sinks(None)

    def test_same_session_name_isolated_by_workspace(self) -> None:
        self.assertEqual("default", self.session_a.session_name)
        self.assertEqual("default", self.session_b.session_name)
        self.assertNotEqual(self.session_a.session_id, self.session_b.session_id)

    def test_session_context_and_messages_round_trip(self) -> None:
        state = AgentContextState(
            summary="workspace A summary",
            recent_messages=[HumanMessage(content="hello"), AIMessage(content="world")],
        )
        self.store.save_session(self.session_a, state, 3)
        message_ids = self.store.archive_turn_messages(
            self.session_a,
            4,
            [HumanMessage(content="archived"), AIMessage(content="answer")],
        )
        loaded, turn = self.store.load_session(self.session_a)
        self.assertEqual(3, turn)
        self.assertEqual("workspace A summary", loaded.summary)
        self.assertEqual(2, len(message_ids))

    def test_memory_retrieval_is_workspace_isolated(self) -> None:
        memory_id = uuid4()
        marker = f"workspace-memory-{uuid4().hex}"
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_memories(
                        id, workspace_id, kind, content, tags, importance, confidence
                    ) VALUES (%s, %s, 'project_fact', %s, '[]', 5, 1.0)
                    """,
                    (memory_id, self.workspace_a.workspace_id, marker),
                )
            conn.commit()
        self.assertEqual(1, len(self.store.retrieve_relevant(self.workspace_a.workspace_id, marker)))
        self.assertEqual([], self.store.retrieve_relevant(self.workspace_b.workspace_id, marker))

    def test_new_session_bootstrap_only_uses_current_workspace(self) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_memories(id, workspace_id, kind, content, tags, importance, confidence)
                    VALUES (%s, %s, 'project_fact', 'A bootstrap fact', '[]', 5, 1.0)
                    """,
                    (uuid4(), self.workspace_a.workspace_id),
                )
            conn.commit()
        memories = self.store.retrieve_for_turn(
            self.workspace_a.workspace_id,
            "unrelated first question",
            new_session=True,
        )
        self.assertTrue(any(memory.content == "A bootstrap fact" for memory in memories))
        self.assertEqual(
            [],
            self.store.retrieve_for_turn(
                self.workspace_b.workspace_id,
                "unrelated first question",
                new_session=True,
            ),
        )

    def test_memory_source_cannot_link_a_message_from_another_workspace(self) -> None:
        memory_id = uuid4()
        message_id = self.store.archive_turn_messages(
            self.session_b,
            1,
            [HumanMessage(content="workspace B message")],
        )[0]
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_memories(id, workspace_id, kind, content, tags, importance, confidence)
                    VALUES (%s, %s, 'project_fact', 'Workspace A fact', '[]', 5, 1.0)
                    """,
                    (memory_id, self.workspace_a.workspace_id),
                )
            conn.commit()

            with self.assertRaises(ForeignKeyViolation), conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO agent_memory_sources(workspace_id, memory_id, message_id)
                        VALUES (%s, %s, %s)
                        """,
                        (self.workspace_a.workspace_id, memory_id, message_id),
                    )

    def test_explicit_memory_question_falls_back_within_current_workspace(self) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_memories(id, workspace_id, kind, content, tags, importance, confidence)
                    VALUES (%s, %s, 'project_fact', 'Workspace A durable decision', '[]', 5, 1.0)
                    """,
                    (uuid4(), self.workspace_a.workspace_id),
                )
            conn.commit()
        memories = self.store.retrieve_relevant(self.workspace_a.workspace_id, "你还记得之前的决定吗？")
        self.assertTrue(any(memory.content == "Workspace A durable decision" for memory in memories))
        self.assertEqual(
            [],
            self.store.retrieve_relevant(self.workspace_b.workspace_id, "你还记得之前的决定吗？"),
        )

    def test_event_sink_persists_workspace_and_session_identity(self) -> None:
        run_id = uuid4().hex
        sink = PostgresEventSink(async_write=False)
        try:
            sink.emit(
                AgentEvent(
                    event_type="workspace_test",
                    source="test",
                    workspace_id=self.workspace_a.workspace_id,
                    session_id=self.session_a.session_id,
                    run_id=run_id,
                )
            )
        finally:
            sink.close()
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT workspace_id, session_id FROM agent_events WHERE run_id = %s",
                    (run_id,),
                )
                self.assertEqual(
                    (self.workspace_a.workspace_id, self.session_a.session_id),
                    cur.fetchone(),
                )


if __name__ == "__main__":
    unittest.main()
