import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from src.core.agent.budget import ExecutionBudget, ToolBudgetExceeded
from src.core.agent.models import AgentRunContext, RunLimits
from src.core.context.models import AgentContextState
from src.core.state import ArtifactStore, ExecutionRepository, LocalStateDatabase, LocalStateStore
from src.core.state import CheckpointManager
from src.core.state.workspace import LocalWorkspaceRepository
from src.core.streaming.events import stream_graph_events
from src.core.tools.catalog import ToolRisk


class LocalStateTest(unittest.TestCase):
    def setUp(self):
        self.root = Path("tests/fixtures/workspace_a").resolve()
        self.workspace_root = self.root
        self.database = LocalStateDatabase(":memory:")
        self.addCleanup(self.database.close)
        self.database.initialize()
        self.repository = LocalWorkspaceRepository(self.database)
        self.workspace = self.repository.resolve(str(self.workspace_root))
        self.session, _ = self.repository.resolve_session(self.workspace, "default")

    def test_workspace_and_session_registration_are_stable(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            workspaces = list(
                executor.map(lambda _index: self.repository.resolve(str(self.workspace_root)), range(8))
            )
        self.assertEqual(1, len({workspace.workspace_id for workspace in workspaces}))
        again, _ = self.repository.resolve_session(self.workspace, "default")
        self.assertEqual(self.session.session_id, again.session_id)

    def test_archived_session_is_unavailable_until_hard_deleted(self):
        self.assertTrue(self.repository.archive_session(self.session))
        self.assertFalse(self.repository.archive_session(self.session))
        with self.assertRaisesRegex(RuntimeError, "archived"):
            self.repository.resolve_session(self.workspace, "default")

        existing = self.repository.get_session_by_name(
            self.workspace,
            "default",
            include_archived=True,
        )
        self.assertIsNotNone(existing)
        self.assertTrue(existing[1])

        self.assertTrue(self.repository.delete_session(self.session))
        recreated, _ = self.repository.resolve_session(self.workspace, "default")
        self.assertNotEqual(self.session.session_id, recreated.session_id)

    def test_completed_turn_is_atomic_and_updates_branch_head(self):
        store = LocalStateStore(self.database, projection_enabled=True)
        state = AgentContextState(
            summary="summary",
            recent_messages=[HumanMessage(content="hello"), AIMessage(content="world")],
        )
        message_ids = store.commit_turn(
            self.session,
            1,
            [HumanMessage(content="hello"), AIMessage(content="world")],
            state,
        )
        loaded, turn_index = store.load_session(self.session)
        with self.database.connect() as conn:
            branch = conn.execute(
                "SELECT head_message_id FROM branches WHERE session_id=?",
                (str(self.session.session_id),),
            ).fetchone()
            outbox_count = conn.execute("SELECT count(*) FROM projection_outbox").fetchone()[0]
        self.assertEqual(1, turn_index)
        self.assertEqual("summary", loaded.summary)
        self.assertEqual(message_ids[-1], branch["head_message_id"])
        self.assertEqual(1, outbox_count)

    def test_one_pending_execution_per_session(self):
        executions = ExecutionRepository(self.database)
        pending = executions.begin(self.session, "large task")
        with self.assertRaisesRegex(RuntimeError, "pending execution"):
            executions.begin(self.session, "second task")
        executions.pause(pending.execution_id, "paused_budget", "graph_step_limit", "continue later")
        self.assertEqual("paused_budget", executions.get_pending(self.session).status)
        resumed = executions.resume(self.session)
        self.assertEqual(2, resumed.grant_index)
        executions.discard(self.session)
        self.assertIsNone(executions.get_pending(self.session))

    def test_discard_atomically_releases_attached_error_execution(self):
        executions = ExecutionRepository(self.database)
        pending = executions.begin(self.session, "failed task")
        executions.pause(pending.execution_id, "paused_error", "graph_error", "failed")

        discarded = executions.discard(self.session)

        self.assertEqual(pending.execution_id, discarded.execution_id)
        self.assertIsNone(executions.get_attached(self.session))

    def test_execution_repository_persists_slice_budget_usage(self):
        executions = ExecutionRepository(self.database)
        pending = executions.begin(self.session, "large task")
        slice_id = executions.start_slice(pending.execution_id, 1, 1)
        usage = {"tool_calls": 5, "controlled_executions": 2, "delegations": 1}

        executions.finish_slice(
            slice_id,
            pending.execution_id,
            status="paused_budget",
            stop_reason="graph_step_limit",
            graph_steps_used=20,
            usage=usage,
        )

        updated = executions.get_pending(self.session)
        self.assertEqual(20, updated.graph_steps_used)
        self.assertEqual(5, updated.tool_calls_used)
        self.assertEqual(2, updated.controlled_executions_used)
        self.assertEqual(1, updated.delegations_used)

    def test_terminal_provider_error_releases_session_and_redacts_input(self):
        executions = ExecutionRepository(self.database)
        pending = executions.begin(self.session, "sensitive input")

        executions.terminate(self.session, pending.execution_id, "content_rejected")

        self.assertIsNone(executions.get_attached(self.session))
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT status, stop_reason, original_input, checkpoint_state
                FROM executions WHERE execution_id=?
                """,
                (pending.execution_id,),
            ).fetchone()
        self.assertEqual("discarded", row["status"])
        self.assertEqual("content_rejected", row["stop_reason"])
        self.assertEqual("[REDACTED]", row["original_input"])
        self.assertEqual("cleanup_pending", row["checkpoint_state"])

        next_execution = executions.begin(self.session, "safe input")
        self.assertIsNotNone(next_execution)

    def test_artifacts_are_deduplicated_and_explicitly_collected(self):
        artifacts = ArtifactStore(self.database, Path(".test_tmp") / f"artifacts-{uuid4().hex}")
        with patch("pathlib.Path.write_bytes"), patch("os.replace"), patch("pathlib.Path.unlink"):
            first = artifacts.put("large output")
            second = artifacts.put("large output")
            self.assertEqual(first.artifact_id, second.artifact_id)
            artifacts.add_reference(first.artifact_id, "message", "m-1")
            self.assertEqual(0, artifacts.collect_garbage())
            with self.database.transaction() as conn:
                conn.execute("DELETE FROM artifact_references")
            self.assertEqual(1, artifacts.collect_garbage())

    def test_vague_memory_recall_fallback_respects_retrieval_limit(self):
        store = LocalStateStore(self.database, retrieval_limit=3)
        with self.database.transaction() as conn:
            for index in range(8):
                conn.execute(
                    """
                    INSERT INTO memories(
                        memory_id, workspace_id, kind, content, tags, importance, confidence
                    ) VALUES (?, ?, 'project_fact', ?, '[]', ?, 1.0)
                    """,
                    (
                        str(uuid4()),
                        str(self.workspace.workspace_id),
                        f"durable fact {index}",
                        index,
                    ),
                )

        memories = store.retrieve_relevant(
            self.workspace.workspace_id,
            "你还记得以前的事情吗？",
        )

        self.assertEqual(3, len(memories))

    def test_unknown_message_role_emits_warning_before_archiving_as_unknown(self):
        store = LocalStateStore(self.database)

        class NewMessageType:
            pass

        with patch("src.core.state.store.emit_event") as emit:
            role = store._message_role(NewMessageType())

        self.assertEqual("unknown", role)
        emit.assert_called_once()
        self.assertEqual("unknown_message_role", emit.call_args.args[0])
        self.assertEqual("warning", emit.call_args.kwargs["level"])


class ExecutionBudgetTest(unittest.TestCase):
    def test_risk_budgets_are_independent_from_read_only_calls(self):
        budget = ExecutionBudget(
            max_controlled_executions=1,
            max_delegations=1,
            hard_max_tool_calls=4,
            max_parallel_tool_calls=1,
            max_wall_seconds=10,
        )
        budget.charge("read", ToolRisk.READ_ONLY)
        budget.charge("exec", ToolRisk.CONTROLLED_EXECUTION)
        budget.charge("delegate", ToolRisk.DELEGATION)
        with self.assertRaises(ToolBudgetExceeded):
            budget.charge("exec-again", ToolRisk.CONTROLLED_EXECUTION)
        self.assertEqual(3, budget.snapshot()["tool_calls"])

    def test_parallel_tool_slot_is_bounded(self):
        budget = ExecutionBudget(max_parallel_tool_calls=1, max_wall_seconds=10)
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()

        def first():
            with budget.tool_slot():
                first_entered.set()
                release_first.wait(timeout=1)

        def second():
            with budget.tool_slot():
                second_entered.set()

        first_thread = threading.Thread(target=first)
        second_thread = threading.Thread(target=second)
        first_thread.start()
        self.assertTrue(first_entered.wait(timeout=1))
        second_thread.start()
        self.assertFalse(second_entered.wait(timeout=0.03))
        release_first.set()
        first_thread.join(timeout=1)
        second_thread.join(timeout=1)
        self.assertTrue(second_entered.is_set())


class CheckpointResumeTest(unittest.TestCase):
    def test_uninitialized_manager_rejects_recovery_operations(self):
        manager = CheckpointManager(":memory:")
        with self.assertRaisesRegex(RuntimeError, "initialized"):
            manager.thread_exists("thread")
        with self.assertRaisesRegex(RuntimeError, "initialized"):
            manager.delete_thread("thread")

    def test_graph_continues_from_checkpoint_after_slice_limit(self):
        manager = CheckpointManager(":memory:")
        calls = {"count": 0}

        def work(_state):
            calls["count"] += 1
            return {"messages": [AIMessage(content=f"step {calls['count']}")]}

        def route(_state):
            return "work" if calls["count"] < 5 else "end"

        builder = StateGraph(MessagesState)
        builder.add_node("work", work)
        builder.add_edge(START, "work")
        builder.add_conditional_edges("work", route, {"work": "work", "end": END})
        graph = builder.compile(checkpointer=manager.initialize())
        context = AgentRunContext(
            "run",
            self._session(),
            1,
            RunLimits(max_graph_steps=3, max_tool_calls=100, max_subagent_steps=2),
        )
        try:
            first = list(
                stream_graph_events(
                    graph,
                    [HumanMessage(content="start")],
                    context,
                    checkpoint_thread_id="thread",
                )
            )
            second = list(
                stream_graph_events(
                    graph,
                    None,
                    context,
                    checkpoint_thread_id="thread",
                )
            )
        finally:
            manager.close()

        self.assertEqual("paused", first[-1]["event"])
        self.assertEqual("done", second[-1]["event"])
        self.assertEqual(5, calls["count"])

    def _session(self):
        from src.core.workspace.models import SessionContext, WorkspaceContext

        workspace = WorkspaceContext(uuid4(), Path(".").resolve())
        return SessionContext(uuid4(), "default", workspace)


if __name__ == "__main__":
    unittest.main()
