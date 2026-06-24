import sqlite3
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage, HumanMessage

from src.core.context.manager import AgentContextManager
from src.core.context.models import AgentContextState
from tests.support.model_providers import UnusedModelProvider
from src.core.adapters.sqlite import SQLiteStateUnitOfWorkFactory
from src.core.finalization import CompletedTurnCommitter, TurnFinalizer
from src.core.finalization.models import CompletedTurn
from src.core.maintenance import (
    ExecutionRecoveryCoordinator,
    MaintenanceJobSpec,
    MaintenanceRepository,
    MaintenanceScheduler,
)
from src.core.maintenance.handlers import ContextSummaryHandler
from src.core.state import ExecutionRepository, LocalStateDatabase, LocalStateStore
from src.core.state.migrations import LATEST_SCHEMA_VERSION, apply_local_migrations
from src.core.state.workspace import LocalWorkspaceRepository
from tests.support.session_services import build_session_lifecycle_service


class WakeOnlyScheduler:
    def __init__(self):
        self.wakes = 0

    def wake(self):
        self.wakes += 1


class FinalizationTest(unittest.TestCase):
    def setUp(self):
        self.database = LocalStateDatabase(":memory:")
        self.addCleanup(self.database.close)
        self.database.initialize()
        self.workspaces = LocalWorkspaceRepository(self.database)
        self.workspace = self.workspaces.resolve(str(Path("tests/fixtures/workspace_a").resolve()))
        self.session, _ = self.workspaces.resolve_session(self.workspace, "default")
        self.store = LocalStateStore(self.database, UnusedModelProvider())
        self.executions = ExecutionRepository(self.database)
        self.maintenance = MaintenanceRepository(self.database)
        self.scheduler = WakeOnlyScheduler()
        self.committer = CompletedTurnCommitter(
            SQLiteStateUnitOfWorkFactory(
                self.database,
                self.executions,
                self.maintenance,
            ),
        )
        self.finalizer = TurnFinalizer(
            AgentContextManager(UnusedModelProvider()),
            self.committer,
            self.scheduler,
        )

    def test_minimal_commit_is_atomic_and_enqueues_derived_work(self):
        execution = self.executions.begin(self.session, "remember this")
        slice_id = self.executions.start_slice(execution.execution_id, 1, 1)
        state = AgentContextState()
        final_messages = [
            HumanMessage(content="please remember this"),
            AIMessage(content="noted"),
        ]

        result = self.finalizer.finalize(
            session=self.session,
            turn_index=1,
            previous_state=state,
            final_messages=final_messages,
            user_input="please remember this",
            execution=execution,
            slice_id=slice_id,
            graph_steps_used=2,
            usage={"tool_calls": 1},
        )

        loaded, turn_index = self.store.load_session(self.session)
        with self.database.connect() as conn:
            execution_row = conn.execute(
                "SELECT status, checkpoint_state FROM executions WHERE execution_id=?",
                (execution.execution_id,),
            ).fetchone()
            pending_id = conn.execute(
                "SELECT pending_execution_id FROM sessions WHERE session_id=?",
                (str(self.session.session_id),),
            ).fetchone()[0]
            jobs = conn.execute(
                "SELECT job_type FROM maintenance_jobs ORDER BY priority DESC"
            ).fetchall()
            message_execution_ids = {
                row[0] for row in conn.execute("SELECT execution_id FROM messages").fetchall()
            }

        self.assertEqual(1, turn_index)
        self.assertEqual(2, len(loaded.recent_messages))
        self.assertEqual("completed", execution_row["status"])
        self.assertEqual("cleanup_pending", execution_row["checkpoint_state"])
        self.assertIsNone(pending_id)
        self.assertEqual(
            ["checkpoint_cleanup", "memory_extract", "context_summary"],
            [row["job_type"] for row in jobs],
        )
        self.assertEqual({execution.execution_id}, message_execution_ids)
        self.assertEqual("pending", result.memory_status)
        self.assertTrue(result.memory_request_explicit)
        self.assertEqual(1, self.scheduler.wakes)

    def test_job_enqueue_failure_rolls_back_entire_completed_turn(self):
        execution = self.executions.begin(self.session, "task")
        slice_id = self.executions.start_slice(execution.execution_id, 1, 1)

        class FailingMaintenanceRepository(MaintenanceRepository):
            def enqueue_in_transaction(self, conn, spec):
                raise RuntimeError("injected enqueue failure")

        committer = CompletedTurnCommitter(
            SQLiteStateUnitOfWorkFactory(
                self.database,
                self.executions,
                FailingMaintenanceRepository(self.database),
            ),
        )
        completed = CompletedTurn(
            self.session,
            1,
            [HumanMessage(content="hello"), AIMessage(content="answer")],
            AgentContextState(recent_messages=[HumanMessage(content="hello")]),
            execution_id=execution.execution_id,
            checkpoint_thread_id=execution.checkpoint_thread_id,
            slice_id=slice_id,
            jobs=(
                MaintenanceJobSpec(
                    "context_summary",
                    "context-summary-fail",
                    str(self.workspace.workspace_id),
                    str(self.session.session_id),
                ),
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "injected enqueue failure"):
            committer.commit(completed)

        with self.database.connect() as conn:
            self.assertEqual(0, conn.execute("SELECT count(*) FROM messages").fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT turn_index FROM sessions").fetchone()[0])
            row = conn.execute(
                "SELECT status FROM executions WHERE execution_id=?",
                (execution.execution_id,),
            ).fetchone()
        self.assertEqual("running", row["status"])

    def test_summary_cas_does_not_overwrite_newer_summary(self):
        self.assertTrue(
            self.store.update_summary_cas(
                self.session,
                expected_summary_through_turn=0,
                summary_through_turn=2,
                summary="new summary",
            )
        )
        self.assertFalse(
            self.store.update_summary_cas(
                self.session,
                expected_summary_through_turn=0,
                summary_through_turn=1,
                summary="stale summary",
            )
        )

    def test_fast_turn_commit_does_not_overwrite_background_summary(self):
        self.assertTrue(
            self.store.update_summary_cas(
                self.session,
                expected_summary_through_turn=0,
                summary_through_turn=1,
                summary="background summary",
            )
        )
        self.finalizer.finalize(
            session=self.session,
            turn_index=2,
            previous_state=AgentContextState(summary="stale summary"),
            final_messages=[HumanMessage(content="next"), AIMessage(content="answer")],
            user_input="next",
        )

        loaded, turn_index = self.store.load_session(self.session)
        self.assertEqual("background summary", loaded.summary)
        self.assertEqual(2, turn_index)

    def test_minimal_commit_remains_a_required_durability_barrier(self):
        class SlowCommitter:
            def commit(self, _completed):
                time.sleep(0.08)
                return []

        finalizer = TurnFinalizer(
            AgentContextManager(UnusedModelProvider()),
            SlowCommitter(),
            self.scheduler,
        )
        started = time.perf_counter()
        finalizer.finalize(
            session=self.session,
            turn_index=1,
            previous_state=AgentContextState(),
            final_messages=[HumanMessage(content="hello"), AIMessage(content="answer")],
            user_input="hello",
        )
        self.assertGreaterEqual(time.perf_counter() - started, 0.07)

    def test_failed_scheduler_wake_does_not_change_committed_turn_result(self):
        class FailingWakeScheduler:
            def wake(self):
                raise RuntimeError("wake failed")

        finalizer = TurnFinalizer(
            AgentContextManager(UnusedModelProvider()),
            self.committer,
            FailingWakeScheduler(),
            memory_enabled=False,
        )
        with patch("src.core.finalization.service.record_error") as record:
            result = finalizer.finalize(
                session=self.session,
                turn_index=1,
                previous_state=AgentContextState(),
                final_messages=[
                    HumanMessage(content="hello"),
                    AIMessage(content="answer"),
                ],
                user_input="hello",
            )

        _state, turn_index = self.store.load_session(self.session)
        self.assertEqual(1, turn_index)
        self.assertEqual("pending", result.maintenance_status)
        record.assert_called_once()

    def test_slow_maintenance_does_not_delay_response_release_or_next_turn(self):
        handler_started = threading.Event()
        release_handler = threading.Event()

        def slow_handler(_job):
            handler_started.set()
            release_handler.wait(timeout=2)

        scheduler = MaintenanceScheduler(
            self.maintenance,
            {"context_summary": slow_handler},
            poll_interval_seconds=0.01,
        )
        finalizer = TurnFinalizer(
            AgentContextManager(UnusedModelProvider()),
            self.committer,
            scheduler,
            memory_enabled=False,
        )
        scheduler.start()
        durations = []
        try:
            state = AgentContextState()
            for turn_index in range(1, 21):
                final_messages = [
                    *state.recent_messages,
                    HumanMessage(content=f"question {turn_index}"),
                    AIMessage(content=f"answer {turn_index}"),
                ]
                started = time.perf_counter()
                finalizer.finalize(
                    session=self.session,
                    turn_index=turn_index,
                    previous_state=state,
                    final_messages=final_messages,
                    user_input=f"question {turn_index}",
                )
                durations.append(time.perf_counter() - started)
                state, completed_turn = self.store.load_session(self.session)
                self.assertEqual(turn_index, completed_turn)
            self.assertTrue(handler_started.wait(timeout=1))
            p95 = sorted(durations)[int(len(durations) * 0.95) - 1]
            self.assertLess(p95, 0.25)
        finally:
            release_handler.set()
            scheduler.close(timeout_seconds=1)


class MaintenanceSchedulerTest(unittest.TestCase):
    def setUp(self):
        self.database = LocalStateDatabase(":memory:")
        self.addCleanup(self.database.close)
        self.database.initialize()
        repository = LocalWorkspaceRepository(self.database)
        workspace = repository.resolve(str(Path("tests/fixtures/workspace_a").resolve()))
        self.session, _ = repository.resolve_session(workspace, "default")
        self.repository = MaintenanceRepository(self.database)

    def spec(self, key="job"):
        return MaintenanceJobSpec(
            "test",
            key,
            str(self.session.workspace.workspace_id),
            str(self.session.session_id),
        )

    def test_dedupe_and_successful_dispatch(self):
        first = self.repository.enqueue(self.spec())
        second = self.repository.enqueue(self.spec())
        handled = []
        scheduler = MaintenanceScheduler(
            self.repository,
            {"test": lambda job: handled.append(job.job_id)},
        )

        self.assertTrue(scheduler.run_once())
        self.assertFalse(scheduler.run_once())
        self.assertEqual(first, second)
        self.assertEqual([first], handled)
        self.assertEqual("succeeded", self.repository.get_by_dedupe_key("job").status)

    def test_expired_lease_is_reclaimed(self):
        self.repository.enqueue(self.spec("leased"))
        first = self.repository.claim_next(lease_seconds=1)
        with self.database.transaction() as conn:
            conn.execute(
                "UPDATE maintenance_jobs SET lease_expires_at=datetime('now', '-1 second')"
            )
        second = self.repository.claim_next(lease_seconds=1)
        self.assertEqual(first.job_id, second.job_id)
        self.assertEqual(2, second.attempts)

    def test_slow_handler_is_not_part_of_turn_finalization(self):
        self.repository.enqueue(self.spec("slow"))
        scheduler = MaintenanceScheduler(
            self.repository,
            {"test": lambda _job: time.sleep(0.2)},
        )
        started = time.perf_counter()
        scheduler.wake()
        self.assertLess(time.perf_counter() - started, 0.05)

    def test_failed_job_retries_then_can_be_requeued_by_recovery(self):
        self.repository.enqueue(
            MaintenanceJobSpec(
                "test",
                "retry",
                str(self.session.workspace.workspace_id),
                str(self.session.session_id),
                max_attempts=1,
            )
        )
        scheduler = MaintenanceScheduler(
            self.repository,
            {"test": lambda _job: (_ for _ in ()).throw(RuntimeError("failed"))},
        )
        self.assertTrue(scheduler.run_once())
        self.assertEqual("failed", self.repository.get_by_dedupe_key("retry").status)
        self.assertTrue(self.repository.requeue_failed("retry"))
        self.assertEqual("pending", self.repository.get_by_dedupe_key("retry").status)

    def test_worker_loop_survives_repository_failure(self):
        called_twice = threading.Event()

        class FlakyRepository:
            def __init__(self):
                self.calls = 0

            def claim_next(self, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise sqlite3.OperationalError("temporary lock")
                called_twice.set()
                return None

        scheduler = MaintenanceScheduler(
            FlakyRepository(),
            {},
            poll_interval_seconds=0.01,
        )
        with patch("src.core.maintenance.scheduler.record_error"):
            scheduler.start()
            try:
                self.assertTrue(called_twice.wait(timeout=1))
            finally:
                self.assertTrue(scheduler.close(timeout_seconds=1))

    def test_timed_out_close_keeps_worker_reference_for_later_join(self):
        started = threading.Event()
        release = threading.Event()
        self.repository.enqueue(self.spec("blocking"))

        def blocking_handler(_job):
            started.set()
            release.wait(timeout=2)

        scheduler = MaintenanceScheduler(
            self.repository,
            {"test": blocking_handler},
            poll_interval_seconds=0.01,
        )
        scheduler.start()
        self.assertTrue(started.wait(timeout=1))
        self.assertFalse(scheduler.close(timeout_seconds=0))
        self.assertIsNotNone(scheduler._thread)
        release.set()
        self.assertTrue(scheduler.close(timeout_seconds=1))
        self.assertIsNone(scheduler._thread)

    def test_concurrent_start_calls_create_exactly_one_worker(self):
        scheduler = MaintenanceScheduler(
            self.repository,
            {},
            poll_interval_seconds=0.05,
        )
        barrier = threading.Barrier(8)
        observed_threads = []
        observed_lock = threading.Lock()

        def start_scheduler():
            barrier.wait()
            scheduler.start()
            with observed_lock:
                observed_threads.append(scheduler._thread)

        callers = [threading.Thread(target=start_scheduler) for _ in range(8)]
        for caller in callers:
            caller.start()
        for caller in callers:
            caller.join(timeout=1)
        try:
            self.assertEqual(1, len({id(thread) for thread in observed_threads}))
        finally:
            self.assertTrue(scheduler.close(timeout_seconds=1))

    def test_start_during_close_does_not_replace_worker(self):
        started = threading.Event()
        release = threading.Event()
        close_finished = threading.Event()
        self.repository.enqueue(self.spec("closing"))

        def blocking_handler(_job):
            started.set()
            release.wait(timeout=2)

        scheduler = MaintenanceScheduler(
            self.repository,
            {"test": blocking_handler},
            poll_interval_seconds=0.01,
        )
        scheduler.start()
        self.assertTrue(started.wait(timeout=1))
        original_thread = scheduler._thread

        def close_scheduler():
            scheduler.close(timeout_seconds=1)
            close_finished.set()

        closer = threading.Thread(target=close_scheduler)
        closer.start()
        while not scheduler._closing:
            time.sleep(0.001)
        scheduler.start()
        self.assertIs(original_thread, scheduler._thread)
        release.set()
        closer.join(timeout=1)
        self.assertTrue(close_finished.is_set())
        self.assertIsNone(scheduler._thread)


class MaintenanceHandlerTest(unittest.TestCase):
    def test_context_summary_uses_injected_recent_message_limit(self):
        session = Mock()

        class Store:
            def __init__(self):
                self.updated = None

            def load_summary_source(self, _session, _target_turn):
                return (
                    "",
                    0,
                    [
                        (1, HumanMessage(content="one")),
                        (2, HumanMessage(content="two")),
                        (3, HumanMessage(content="three")),
                    ],
                )

            def update_summary_cas(self, _session, **kwargs):
                self.updated = kwargs
                return True

        class Context:
            recent_message_limit = 1

            def should_summarize(self, _messages):
                return True

            def summarize_messages(self, _summary, messages):
                return ",".join(message.content for message in messages)

        store = Store()
        handler = ContextSummaryHandler(
            Mock(get_session=Mock(return_value=session)),
            store,
            Context(),
        )
        handler(
            Mock(
                workspace_id="workspace",
                session_id="session",
                payload={"target_turn": 3},
            )
        )

        self.assertEqual("one,two", store.updated["summary"])
        self.assertEqual(2, store.updated["summary_through_turn"])


class RecoveryCoordinatorTest(unittest.TestCase):
    def setUp(self):
        self.database = LocalStateDatabase(":memory:")
        self.addCleanup(self.database.close)
        self.database.initialize()
        self.workspaces = LocalWorkspaceRepository(self.database)
        self.workspace = self.workspaces.resolve(str(Path("tests/fixtures/workspace_a").resolve()))
        self.executions = ExecutionRepository(self.database)
        self.maintenance = MaintenanceRepository(self.database)

    def test_reconciles_existing_and_missing_checkpoints(self):
        recoverable_session, _ = self.workspaces.resolve_session(self.workspace, "recoverable")
        missing_session, _ = self.workspaces.resolve_session(self.workspace, "missing")
        recoverable = self.executions.begin(recoverable_session, "resume me")
        missing = self.executions.begin(missing_session, "lost")

        class Checkpoints:
            def thread_exists(self, thread_id):
                return thread_id == recoverable.checkpoint_thread_id

        result = ExecutionRecoveryCoordinator(
            self.executions,
            Checkpoints(),
            self.maintenance,
        ).reconcile()

        self.assertEqual(1, result["paused_recovery"])
        self.assertEqual(1, result["missing"])
        self.assertTrue(self.executions.get_attached(recoverable_session).recoverable)
        self.assertEqual(
            "unrecoverable_checkpoint",
            self.executions.get_attached(missing_session).status,
        )
        with self.assertRaisesRegex(RuntimeError, "checkpoint is unavailable"):
            self.executions.resume(missing_session)

    def test_session_status_exposes_checkpoint_and_maintenance_state(self):
        session, _ = self.workspaces.resolve_session(self.workspace, "status")
        execution = self.executions.begin(session, "task")
        self.executions.pause(execution.execution_id, "paused_budget", "graph_step_limit")
        self.maintenance.enqueue(
            MaintenanceJobSpec(
                "context_summary",
                "status-summary",
                str(self.workspace.workspace_id),
                str(session.session_id),
            )
        )
        service = build_session_lifecycle_service(
            database=self.database,
            workspace_repository=self.workspaces,
            execution_repository=self.executions,
            maintenance_repository=self.maintenance,
        )
        status = service.session_status(str(self.workspace.root), "status")

        self.assertTrue(status["execution_recoverable"])
        self.assertEqual("available", status["checkpoint_state"])
        self.assertEqual(1, status["maintenance"]["pending"])

    def test_completed_execution_recreates_checkpoint_cleanup_job(self):
        session, _ = self.workspaces.resolve_session(self.workspace, "completed")
        execution = self.executions.begin(session, "task")
        self.executions.complete(session, execution.execution_id)

        checkpoints = Mock()
        result = ExecutionRecoveryCoordinator(
            self.executions,
            checkpoints,
            self.maintenance,
        ).reconcile()

        job = self.maintenance.get_by_dedupe_key(
            f"checkpoint_cleanup:{execution.execution_id}"
        )
        self.assertEqual(1, result["cleanup_enqueued"])
        self.assertIsNotNone(job)
        self.assertEqual("pending", job.status)
        checkpoints.thread_exists.assert_not_called()

    def test_cleaned_completed_execution_is_excluded_from_recovery_scan(self):
        session, _ = self.workspaces.resolve_session(self.workspace, "cleaned")
        execution = self.executions.begin(session, "task")
        self.executions.complete(session, execution.execution_id)
        self.executions.mark_checkpoint_cleaned(execution.execution_id)

        checkpoints = Mock()
        result = ExecutionRecoveryCoordinator(
            self.executions,
            checkpoints,
            self.maintenance,
        ).reconcile()

        self.assertEqual(
            {"paused_recovery": 0, "missing": 0, "cleanup_enqueued": 0},
            result,
        )
        checkpoints.thread_exists.assert_not_called()


class LocalSchemaMigrationTest(unittest.TestCase):
    def test_in_memory_database_enforces_foreign_keys(self):
        database = LocalStateDatabase(":memory:")
        self.addCleanup(database.close)
        database.initialize()
        with database.connect() as conn:
            self.assertEqual(1, conn.execute("PRAGMA foreign_keys").fetchone()[0])

    def test_existing_v1_database_is_upgraded_additively(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE local_schema_migrations(version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT);
            INSERT INTO local_schema_migrations VALUES (1, 'local_first_state', CURRENT_TIMESTAMP);
            CREATE TABLE sessions(session_id TEXT PRIMARY KEY);
            CREATE TABLE executions(execution_id TEXT PRIMARY KEY);
            """
        )
        apply_local_migrations(conn)
        session_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
        execution_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(executions)")
        }
        version = conn.execute("SELECT max(version) FROM local_schema_migrations").fetchone()[0]
        conn.close()

        self.assertIn("summary_through_turn", session_columns)
        self.assertIn("checkpoint_state", execution_columns)
        self.assertIn("completed_at", execution_columns)
        self.assertIn("goal_mode", execution_columns)
        self.assertGreaterEqual(version, 4)
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE local_schema_migrations(version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT);
            INSERT INTO local_schema_migrations VALUES (2, 'old', CURRENT_TIMESTAMP);
            CREATE TABLE executions(
                execution_id TEXT PRIMARY KEY,
                checkpoint_state TEXT NOT NULL DEFAULT 'uninitialized'
            );
            """
        )
        apply_local_migrations(conn)
        conn.execute("INSERT INTO executions(execution_id) VALUES ('execution')")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE executions SET checkpoint_state='invented' WHERE execution_id='execution'"
            )
        conn.close()

    def test_newer_local_schema_version_is_rejected(self):
        database = LocalStateDatabase(":memory:")
        self.addCleanup(database.close)
        database.initialize()
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO local_schema_migrations(version, name) VALUES (?, 'future')",
                (LATEST_SCHEMA_VERSION + 1,),
            )

        with self.assertRaisesRegex(RuntimeError, "newer"):
            database.initialize()

    def test_state_validation_migration_rejects_existing_unknown_status(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE local_schema_migrations(version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT);
            INSERT INTO local_schema_migrations VALUES (2, 'old', CURRENT_TIMESTAMP);
            CREATE TABLE maintenance_jobs(status TEXT NOT NULL);
            INSERT INTO maintenance_jobs(status) VALUES ('invented');
            """
        )

        with self.assertRaisesRegex(RuntimeError, "unsupported maintenance_jobs.status"):
            apply_local_migrations(conn)

        version = conn.execute(
            "SELECT max(version) FROM local_schema_migrations"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(2, version)

    def test_initialize_rolls_back_schema_upgrade_when_migration_fails(self):
        database = LocalStateDatabase(":memory:")
        self.addCleanup(database.close)
        database.initialize()
        workspaces = LocalWorkspaceRepository(database)
        workspace = workspaces.resolve(str(Path("tests/fixtures/workspace_a").resolve()))
        session, _ = workspaces.resolve_session(workspace, "migration")
        with database.transaction() as conn:
            conn.execute("DELETE FROM local_schema_migrations WHERE version > 1")
            conn.execute(
                "UPDATE sessions SET turn_index=1 WHERE session_id=?",
                (str(session.session_id),),
            )

        def failing_migration(conn):
            conn.execute("UPDATE sessions SET turn_index=99")
            raise RuntimeError("injected migration failure")

        with patch(
            "src.core.state.database.apply_local_migrations",
            side_effect=failing_migration,
        ):
            with self.assertRaisesRegex(RuntimeError, "injected migration failure"):
                database.initialize()

        with database.connect() as conn:
            version = conn.execute(
                "SELECT max(version) FROM local_schema_migrations"
            ).fetchone()[0]
            turn_index = conn.execute(
                "SELECT turn_index FROM sessions WHERE session_id=?",
                (str(session.session_id),),
            ).fetchone()[0]
        self.assertEqual(1, version)
        self.assertEqual(1, turn_index)


if __name__ == "__main__":
    unittest.main()
