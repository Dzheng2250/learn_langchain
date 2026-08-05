import asyncio
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage

from src.core.agent.coordinator import PreparedTurn
from src.core.context.models import AgentContextState
from src.core.errors import ErrorCategory
from src.core.llm.provider import LlmConfigurationStatus
from src.core.maintenance.repository import MaintenanceRepository
from src.config.settings import CORE_AGENT_WORKERS
from src.core.agent.locking import SessionLockRegistry
from tests.support.agent_services import build_agent_turn_service
from src.core.agent.worker import TurnWorkerExecutor
from tests.support.session_services import build_session_lifecycle_service
from src.core.state import ExecutionRepository, LocalStateDatabase, LocalStateStore
from src.core.state.workspace import LocalWorkspaceRepository
from src.core.workspace.models import SessionContext, WorkspaceContext
from tests.support.model_providers import UnusedModelProvider


class SessionLockRegistryTest(unittest.TestCase):
    def test_same_internal_session_is_serialized(self):
        registry = SessionLockRegistry()
        session_id = uuid4()
        active = 0
        max_active = 0
        guard = threading.Lock()

        def worker():
            nonlocal active, max_active
            with registry.get(session_id):
                with guard:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.04)
                with guard:
                    active -= 1

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(1, max_active)

    def test_same_name_can_run_concurrently_with_different_internal_ids(self):
        registry = SessionLockRegistry()
        barrier = threading.Barrier(2)
        active = 0
        max_active = 0
        guard = threading.Lock()

        def worker(session_id):
            nonlocal active, max_active
            with registry.get(session_id):
                barrier.wait()
                with guard:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.03)
                with guard:
                    active -= 1

        threads = [threading.Thread(target=worker, args=(uuid4(),)) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(2, max_active)


class AgentTurnExecutorTest(unittest.IsolatedAsyncioTestCase):
    def _service(self, executor=None, max_concurrent_turns=CORE_AGENT_WORKERS, sync_runner=None):
        worker = TurnWorkerExecutor(
            executor=executor,
            max_workers=max_concurrent_turns,
        )
        return build_agent_turn_service(
            workspace_repository=Mock(),
            runtime_registry=Mock(),
            state_store_factory=Mock(),
            turn_worker=worker,
            max_concurrent_turns=max_concurrent_turns,
            sync_turn_runner=sync_runner,
        )

    async def test_injected_executor_bounds_concurrent_turns(self):
        executor = ThreadPoolExecutor(max_workers=2)
        service = self._service(executor, max_concurrent_turns=2)
        active = 0
        max_active = 0
        guard = threading.Lock()

        def run_sync(*_args, **_kwargs):
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.04)
            with guard:
                active -= 1
            return {"status": "ok"}

        service.async_turn_runner.sync_runner.run_turn = run_sync
        try:
            await asyncio.gather(*(service.run_turn(".", f"s-{index}", "hello") for index in range(5)))
        finally:
            service.close()
            executor.shutdown()

        self.assertEqual(2, max_active)

    async def test_service_does_not_close_injected_executor(self):
        executor = ThreadPoolExecutor(max_workers=1)
        service = self._service(executor)
        service.close()
        try:
            self.assertEqual("still-open", executor.submit(lambda: "still-open").result())
        finally:
            executor.shutdown()

    async def test_default_executor_uses_configured_worker_limit(self):
        service = self._service()
        try:
            self.assertEqual(CORE_AGENT_WORKERS, service.async_turn_runner.turn_worker._executor._max_workers)
        finally:
            service.close()

    async def test_rejects_invalid_concurrency_limit(self):
        with self.assertRaises(ValueError):
            self._service(max_concurrent_turns=0)

    async def test_cancelled_waiter_keeps_slot_until_worker_finishes(self):
        executor = ThreadPoolExecutor(max_workers=2)
        service = self._service(executor, max_concurrent_turns=1)
        first_started = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        calls = 0

        def run_sync(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                first_started.set()
                release_first.wait(timeout=1)
            else:
                second_started.set()
            return {"status": "ok"}

        service.async_turn_runner.sync_runner.run_turn = run_sync
        first = asyncio.create_task(service.run_turn(".", "first", "hello"))
        self.assertTrue(await asyncio.to_thread(first_started.wait, 1))
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first

        second = asyncio.create_task(service.run_turn(".", "second", "hello"))
        await asyncio.sleep(0.02)
        self.assertFalse(second_started.is_set())
        release_first.set()
        await second

        service.close()
        executor.shutdown()


class DiagnosticTurnTest(unittest.TestCase):
    def test_missing_api_key_does_not_mutate_conversation_or_create_runtime(self):
        workspace = WorkspaceContext(uuid4(), Path(".").resolve())
        session = SessionContext(uuid4(), "default", workspace)

        class MissingConfiguration:
            def configuration_status(self):
                return LlmConfigurationStatus(False, ("LEARN_AGENT_LLM_API_KEY",))

        class Repository:
            def resolve(self, _root):
                return workspace

            def resolve_session(self, _workspace, _name):
                return session, True

        class Store:
            def __init__(self):
                self.loaded = 0
                self.closed = False

            def load_context(self, _session):
                self.loaded += 1
                return AgentContextState(), 0

            def archive_turn_messages(self, _session, turn_index, messages):
                raise AssertionError("diagnostic requests must not archive messages")

            def save_session(self, _session, state, turn_index):
                raise AssertionError("diagnostic requests must not update session state")

            def close(self):
                self.closed = True

        store = Store()
        runtime_registry = Mock()
        service = build_agent_turn_service(
            workspace_repository=Repository(),
            runtime_registry=runtime_registry,
            state_store_factory=lambda: store,
            model_configuration=MissingConfiguration(),
        )
        try:
            with patch("src.core.diagnostics.turn.emit_event") as emit:
                first = list(service.stream_turn(".", "default", "检查连接", run_id="run-1"))
                second = list(service.stream_turn(".", "default", "再次检查", run_id="run-2"))
        finally:
            service.close()

        event_types = [call.args[0] for call in emit.call_args_list]
        self.assertEqual(["token", "done"], [event["event"] for event in first])
        self.assertEqual(["token", "done"], [event["event"] for event in second])
        self.assertEqual("llm_not_configured", first[-1]["data"]["stop_reason"])
        self.assertIn("LEARN_AGENT_LLM_API_KEY", first[0]["data"]["content"])
        self.assertEqual(first[-1]["data"]["session_id"], second[-1]["data"]["session_id"])
        self.assertEqual(2, event_types.count("diagnostic_started"))
        self.assertEqual(2, event_types.count("diagnostic_finished"))
        self.assertNotIn("turn_started", event_types)
        self.assertNotIn("turn_finished", event_types)
        runtime_registry.get.assert_not_called()
        self.assertEqual(2, store.loaded)
        self.assertFalse(store.closed)


class GoalModeRoutingTest(unittest.TestCase):
    def setUp(self):
        self.database = LocalStateDatabase(":memory:")
        self.addCleanup(self.database.close)
        self.database.initialize()
        self.workspace_repository = LocalWorkspaceRepository(self.database)
        self.execution_repository = ExecutionRepository(self.database)

    def _service(self, runtime):
        class ConfiguredModel:
            def configuration_status(self):
                return LlmConfigurationStatus(True)

        class Store:
            def load_context(self, _session):
                return AgentContextState(), 0

            def retrieve_for_turn(self, *_args, **_kwargs):
                return []

            def build_memory_message(self, _memories):
                return None

            def close(self):
                pass

        class Coordinator:
            def prepare(self, *, session, run_id, limits, **_kwargs):
                return PreparedTurn(
                    AgentContextState(),
                    1,
                    Mock(run_id=run_id, session=session, turn_index=1, limits=limits),
                    [HumanMessage(content="hello")],
                )

            def finalize(self, **_kwargs):
                return Mock(
                    maintenance_status="pending",
                    memory_status="skipped",
                    memory_request_explicit=False,
                )

        registry = Mock()
        registry.get.return_value = runtime
        return build_agent_turn_service(
            workspace_repository=self.workspace_repository,
            runtime_registry=registry,
            state_store_factory=Store,
            model_configuration=ConfiguredModel(),
            execution_repository=self.execution_repository,
            turn_coordinator=Coordinator(),
            max_auto_slices=1,
        )

    def test_goal_mode_uses_the_same_stable_graph(self):
        class RecordingGraph:
            def __init__(self, name):
                self.name = name
                self.calls = 0
                self.inputs = []

            def stream(self, inputs, **_kwargs):
                self.calls += 1
                self.inputs.append(inputs)
                yield "values", {"messages": [*inputs["messages"], AIMessage(content=self.name)]}

        normal = RecordingGraph("normal")
        goal = RecordingGraph("goal")
        service = self._service(Mock(graph=normal))
        try:
            list(
                service.stream_turn(
                    str(Path("tests/fixtures/workspace_a").resolve()),
                    "default",
                    "hello",
                    run_id="normal-run",
                )
            )
            list(
                service.stream_turn(
                    str(Path("tests/fixtures/workspace_a").resolve()),
                    "goal-session",
                    "build feature",
                    run_id="goal-run",
                    goal_mode=True,
                )
            )
        finally:
            service.close()

        self.assertEqual(2, normal.calls)
        self.assertEqual(0, goal.calls)
        self.assertNotIn("<goal-mode>", normal.inputs[0]["messages"][-1].content)
        self.assertIn("<goal-mode>", normal.inputs[1]["messages"][-1].content)

    def test_run_turn_accepts_goal_mode_and_uses_stable_graph(self):
        class RecordingGraph:
            def __init__(self, name):
                self.name = name
                self.calls = 0

            def stream(self, inputs, **_kwargs):
                self.calls += 1
                yield "values", {"messages": [*inputs["messages"], AIMessage(content=self.name)]}

        normal = RecordingGraph("normal")
        goal = RecordingGraph("goal")
        service = self._service(Mock(graph=normal))
        try:
            result = asyncio.run(
                service.run_turn(
                    str(Path("tests/fixtures/workspace_a").resolve()),
                    "goal-run-turn",
                    "build feature",
                    run_id="goal-run-turn",
                    goal_mode=True,
                )
            )
        finally:
            service.close()

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, normal.calls)
        self.assertEqual(0, goal.calls)

    def test_new_chat_with_pending_execution_returns_recoverable_pause(self):
        class PausingGraph:
            def stream(self, *_args, **_kwargs):
                yield "values", {"messages": [AIMessage(content="", tool_calls=[])]}
                from src.core.agent.budget import ToolBudgetExceeded

                raise ToolBudgetExceeded("test budget exhausted")

            def get_state(self, _config):
                return Mock(values={"messages": []})

        graph = PausingGraph()
        service = self._service(Mock(graph=graph))
        workspace_root = str(Path("tests/fixtures/workspace_a").resolve())
        try:
            first = list(
                service.stream_turn(
                    workspace_root,
                    "pending-chat",
                    "large goal",
                    run_id="first",
                    goal_mode=True,
                )
            )
            second = list(
                service.stream_turn(
                    workspace_root,
                    "pending-chat",
                    "new message",
                    run_id="second",
                )
            )
        finally:
            service.close()

        self.assertEqual("paused", first[-1]["data"]["status"])
        self.assertEqual("done", second[-1]["event"])
        self.assertEqual("paused", second[-1]["data"]["status"])
        self.assertIn("session resume", second[-1]["data"]["message"])
        self.assertTrue(second[-1]["data"]["goal_mode"])

    def test_resume_without_pending_execution_returns_idle(self):
        service = self._service(Mock(graph=Mock()))
        workspace_root = str(Path("tests/fixtures/workspace_a").resolve())
        try:
            events = list(
                service.stream_resume(
                    workspace_root,
                    "idle-resume",
                    run_id="idle-resume-run",
                )
            )
        finally:
            service.close()

        self.assertEqual("done", events[-1]["event"])
        self.assertEqual("idle", events[-1]["data"]["status"])
        self.assertIn("no pending execution", events[-1]["data"]["message"])

    def test_discard_without_pending_execution_returns_idle(self):
        workspace_root = str(Path("tests/fixtures/workspace_a").resolve())
        service = build_session_lifecycle_service(
            database=self.database,
            workspace_repository=self.workspace_repository,
            execution_repository=self.execution_repository,
        )
        result = service.discard_pending(workspace_root, "idle-discard")

        self.assertEqual("idle", result["status"])
        self.assertIn("no pending execution", result["message"])

    def test_resume_uses_stable_graph_for_goal_execution(self):
        class RecordingGraph:
            def __init__(self, name):
                self.name = name
                self.calls = 0
                self.updated = False

            def stream(self, inputs, **_kwargs):
                self.calls += 1
                messages = [] if inputs is None else inputs["messages"]
                yield "values", {"messages": [*messages, AIMessage(content=self.name)]}

            def update_state(self, *_args, **_kwargs):
                self.updated = True

            def get_state(self, _config):
                return Mock(values={"messages": []})

        normal = RecordingGraph("normal")
        goal = RecordingGraph("goal")
        service = self._service(Mock(graph=normal))
        workspace_root = str(Path("tests/fixtures/workspace_a").resolve())
        try:
            list(
                service.stream_turn(
                    workspace_root,
                    "goal-resume",
                    "large goal",
                    run_id="goal-start",
                    goal_mode=True,
                )
            )
            list(
                service.stream_resume(
                    workspace_root,
                    "goal-resume",
                    run_id="goal-resume",
                    instruction="continue",
                    retry_conditions=True,
                )
            )
        finally:
            service.close()

        self.assertEqual(2, normal.calls)
        self.assertEqual(0, goal.calls)
        self.assertTrue(normal.updated)


class ProviderErrorResolutionIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.database = LocalStateDatabase(":memory:")
        self.addCleanup(self.database.close)
        self.database.initialize()
        self.workspace_repository = LocalWorkspaceRepository(self.database)
        self.execution_repository = ExecutionRepository(self.database)

    def test_content_rejection_terminates_execution_and_releases_session(self):
        class ConfiguredModel:
            def configuration_status(self):
                return LlmConfigurationStatus(True)

        class RejectedGraph:
            def stream(self, *_args, **_kwargs):
                raise RuntimeError(
                    "Error code: 400 - {'error': {'code': 'data_inspection_failed'}}"
                )

        runtime_registry = Mock()
        runtime_registry.get.return_value = Mock(graph=RejectedGraph())
        service = build_agent_turn_service(
            workspace_repository=self.workspace_repository,
            runtime_registry=runtime_registry,
            state_store_factory=lambda: LocalStateStore(self.database, UnusedModelProvider()),
            model_configuration=ConfiguredModel(),
            execution_repository=self.execution_repository,
        )
        try:
            events = list(
                service.stream_turn(
                    str(Path("tests/fixtures/workspace_a").resolve()),
                    "default",
                    "rejected input",
                    run_id="run-rejected",
                )
            )
        finally:
            service.close()

        self.assertEqual(["step", "token", "done"], [event["event"] for event in events])
        self.assertIn(
            "失败来源：当前这轮前台对话",
            events[1]["data"]["content"],
        )
        self.assertIn(
            "LLM 调用位置：父 Agent 调用模型服务商",
            events[1]["data"]["content"],
        )
        terminal = events[-1]["data"]
        self.assertEqual("terminated", terminal["status"])
        self.assertTrue(terminal["auto_recovered"])
        self.assertFalse(terminal["failed_turn_saved"])
        self.assertEqual(ErrorCategory.CONTENT_REJECTED, terminal["error_category"])
        self.assertEqual("terminate", terminal["error_action"])
        self.assertEqual("agent_turn", terminal["failure_source"])
        self.assertEqual("parent_model_provider", terminal["failure_stage"])
        self.assertEqual("current_turn", terminal["failure_scope"])
        self.assertEqual("revise_input_and_retry", terminal["user_action"])
        self.assertNotIn("data_inspection_failed", terminal["message"])

        workspace = self.workspace_repository.resolve(
            str(Path("tests/fixtures/workspace_a").resolve())
        )
        session, _ = self.workspace_repository.resolve_session(workspace, "default")
        self.assertIsNone(self.execution_repository.get_attached(session))
        state, turn_index = LocalStateStore(self.database, UnusedModelProvider()).load_session(session)
        self.assertEqual(0, turn_index)
        self.assertEqual([], state.recent_messages)
        next_execution = self.execution_repository.begin(session, "safe input")
        self.assertIsNotNone(next_execution)

    def test_rate_limit_pauses_execution_for_later_resume(self):
        class ConfiguredModel:
            def configuration_status(self):
                return LlmConfigurationStatus(True)

        class RateLimitError(Exception):
            status_code = 429
            body = {"error": {"code": "rate_limit"}}

        class RateLimitedGraph:
            def stream(self, *_args, **_kwargs):
                raise RateLimitError("private provider response")

        runtime_registry = Mock()
        runtime_registry.get.return_value = Mock(graph=RateLimitedGraph())
        service = build_agent_turn_service(
            workspace_repository=self.workspace_repository,
            runtime_registry=runtime_registry,
            state_store_factory=lambda: LocalStateStore(self.database, UnusedModelProvider()),
            model_configuration=ConfiguredModel(),
            execution_repository=self.execution_repository,
        )
        try:
            events = list(
                service.stream_turn(
                    str(Path("tests/fixtures/workspace_a").resolve()),
                    "rate-limited",
                    "request",
                    run_id="run-rate-limit",
                )
            )
        finally:
            service.close()

        error = events[-1]["data"]
        self.assertEqual("pause", error["error_action"])
        self.assertTrue(error["retryable"])
        self.assertNotIn("private provider response", error["message"])

        workspace = self.workspace_repository.resolve(
            str(Path("tests/fixtures/workspace_a").resolve())
        )
        session, _ = self.workspace_repository.resolve_session(workspace, "rate-limited")
        pending = self.execution_repository.get_pending(session)
        self.assertIsNotNone(pending)
        self.assertEqual("paused_error", pending.status)

    def test_session_status_reports_recent_background_maintenance_failures(self):
        workspace = self.workspace_repository.resolve(
            str(Path("tests/fixtures/workspace_a").resolve())
        )
        session, _ = self.workspace_repository.resolve_session(workspace, "default")
        with self.database.transaction() as conn:
            conn.execute(
                """
                INSERT INTO maintenance_jobs(
                    job_id, workspace_id, session_id, job_type, dedupe_key,
                    status, payload, attempts, max_attempts, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "job-memory-failed",
                    str(workspace.workspace_id),
                    str(session.session_id),
                    "memory_extract",
                    "memory_extract:test",
                    "failed",
                    "{}",
                    3,
                    3,
                    "provider rejected background memory extraction",
                ),
            )

        service = build_session_lifecycle_service(
            database=self.database,
            workspace_repository=self.workspace_repository,
            execution_repository=self.execution_repository,
            maintenance_repository=MaintenanceRepository(self.database),
        )
        status = service.session_status(
            str(Path("tests/fixtures/workspace_a").resolve()),
            "default",
        )

        self.assertEqual(1, status["maintenance"]["failed"])
        self.assertEqual(
            "memory_extract",
            status["maintenance"]["recent_failures"][0]["job_type"],
        )
        self.assertIn(
            "background memory extraction",
            status["maintenance"]["recent_failures"][0]["last_error"],
        )

    def test_session_delete_archives_by_default_and_hard_delete_removes_rows(self):
        workspace_root = str(Path("tests/fixtures/workspace_a").resolve())
        workspace = self.workspace_repository.resolve(workspace_root)
        session, _ = self.workspace_repository.resolve_session(workspace, "delete-me")
        with self.database.transaction() as conn:
            conn.execute(
                """
                INSERT INTO messages(
                    message_id, workspace_id, session_id, role, message_type,
                    content, raw, turn_index, message_ordinal
                ) VALUES (?, ?, ?, 'human', 'HumanMessage', 'hello', '{}', 1, 1)
                """,
                (
                    "message-delete-me",
                    str(workspace.workspace_id),
                    str(session.session_id),
                ),
            )
        turn_service = build_agent_turn_service(
            workspace_repository=self.workspace_repository,
            runtime_registry=Mock(),
            state_store_factory=lambda: LocalStateStore(self.database, UnusedModelProvider()),
            execution_repository=self.execution_repository,
        )
        session_service = build_session_lifecycle_service(
            database=self.database,
            workspace_repository=self.workspace_repository,
            execution_repository=self.execution_repository,
            lock_registry=turn_service.request_stream_service.lock_registry,
        )
        try:
            archived = session_service.delete_session(workspace_root, "delete-me")
            blocked = list(
                turn_service.stream_turn(
                    workspace_root,
                    "delete-me",
                    "hello again",
                    run_id="run-archived",
                )
            )
            deleted = session_service.delete_session(
                workspace_root,
                "delete-me",
                hard_delete=True,
            )
        finally:
            turn_service.close()

        self.assertEqual("archived", archived["status"])
        self.assertEqual("archived", blocked[-1]["data"]["status"])
        self.assertEqual("deleted", deleted["status"])
        with self.database.connect() as conn:
            self.assertEqual(
                0,
                conn.execute(
                    "SELECT count(*) FROM messages WHERE session_id=?",
                    (str(session.session_id),),
                ).fetchone()[0],
            )


if __name__ == "__main__":
    unittest.main()
