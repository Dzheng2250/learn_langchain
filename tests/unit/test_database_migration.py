import unittest
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch
from uuid import uuid4

from tests.support.paths import REPOSITORY_ROOT

from src.core.database.migration import MigrationReport, WorkspaceMigration, create_database_backup
from src.core.database.schema import LegacySchemaError, SchemaManager
from src.core.state.migration import LocalStateMigration, LocalStateMigrationReport


ROOT = REPOSITORY_ROOT


class FakeCursor:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        return next(self.rows)


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.transaction_entries = 0
        self.transaction_errors = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor

    def transaction(self):
        connection = self

        class Transaction:
            def __enter__(self):
                connection.transaction_entries += 1
                return self

            def __exit__(self, exc_type, _exc, _traceback):
                connection.transaction_errors.append(exc_type)
                return False

        return Transaction()


class FakePool:
    def __init__(self, cursor):
        self.cursor = cursor
        self.connection_instance = FakeConnection(cursor)

    def connection(self):
        return self.connection_instance


class DatabaseMigrationTest(unittest.TestCase):
    def test_dry_run_only_reads_legacy_counts(self):
        cursor = FakeCursor([(False, True), (1,), (503,), (1611,), (7,)])
        migration = WorkspaceMigration(lambda: FakeConnection(cursor))
        report = migration.inspect(ROOT, "default")
        self.assertFalse(report.applied)
        self.assertEqual((1, 503, 7, 1611), (
            report.sessions,
            report.messages,
            report.memories,
            report.events,
        ))
        statements = " ".join(query for query, _params in cursor.queries).upper()
        self.assertNotIn("DELETE ", statements)
        self.assertNotIn("ALTER TABLE", statements)

    def test_schema_manager_rejects_legacy_schema(self):
        cursor = FakeCursor([(False, True)])
        with self.assertRaises(LegacySchemaError):
            SchemaManager(FakePool(cursor)).initialize()

    def test_schema_manager_upgrades_workspace_memory_source_constraint(self):
        cursor = FakeCursor([(True, True), (True,), (1,)])
        pool = FakePool(cursor)
        SchemaManager(pool).initialize()
        statements = " ".join(str(query) for query, _params in cursor.queries)
        self.assertIn("ALTER TABLE agent_memory_sources", statements)
        self.assertIn("memory_source_workspace", str(cursor.queries))
        self.assertEqual(1, pool.connection_instance.transaction_entries)
        self.assertEqual([None], pool.connection_instance.transaction_errors)

    def test_schema_manager_rejects_a_newer_database_version(self):
        cursor = FakeCursor([(True, True), (True,), (999,)])
        pool = FakePool(cursor)
        with self.assertRaisesRegex(LegacySchemaError, "newer"):
            SchemaManager(pool).initialize()
        self.assertEqual([LegacySchemaError], pool.connection_instance.transaction_errors)

    def test_apply_refuses_to_open_migration_transaction_when_backup_fails(self):
        connections_opened = 0

        class InspectedMigration(WorkspaceMigration):
            def inspect(self, workspace, keep_session):
                return MigrationReport(1, 503, 7, 1611, ROOT)

        def connection_factory():
            nonlocal connections_opened
            connections_opened += 1
            raise AssertionError("migration transaction must not start after backup failure")

        migration = InspectedMigration(connection_factory)
        with (
            patch(
                "src.core.database.migration.create_database_backup",
                side_effect=RuntimeError("backup failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "backup failed"),
        ):
            migration.apply(ROOT, "default")
        self.assertEqual(0, connections_opened)

    def test_apply_rolls_back_when_validation_fails(self):
        cursor = FakeCursor([])
        connection = FakeConnection(cursor)

        class FailingValidationMigration(WorkspaceMigration):
            def inspect(self, workspace, keep_session):
                return MigrationReport(1, 2, 3, 4, ROOT)

            def _require_legacy(self, cur):
                return None

            def _validate_counts(self, cur, expected):
                raise RuntimeError("validation failed")

        migration = FailingValidationMigration(lambda: connection)
        with (
            patch("src.core.database.migration.create_database_backup", return_value=ROOT / "backup.dump"),
            patch("src.core.database.migration.execute_sql_file"),
            self.assertRaisesRegex(RuntimeError, "validation failed"),
        ):
            migration.apply(ROOT, "default")

        self.assertEqual([RuntimeError], connection.transaction_errors)

    def test_backup_supports_connection_url_without_password(self):
        target_dir = ROOT / ".test_tmp" / f"backup-{uuid4().hex}"
        target_dir.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, target_dir, True)

        def fake_run(command, *, check, env, capture_output):
            self.assertTrue(check)
            self.assertTrue(capture_output)
            self.assertNotIn("PGPASSWORD", env)
            Path(command[-1]).write_bytes(b"backup")

        with (
            patch("src.core.database.backup.backup_dir", return_value=target_dir),
            patch(
                "src.core.database.backup.connection_kwargs",
                return_value={
                    "host": "db.example",
                    "port": "5434",
                    "user": "agent",
                    "dbname": "learn_agent",
                },
            ),
            patch("src.core.database.backup.shutil.which", return_value="pg_dump"),
            patch("src.core.database.backup.subprocess.run", side_effect=fake_run),
        ):
            backup = create_database_backup()

        self.assertEqual(b"backup", backup.read_bytes())

    def test_failed_docker_backup_removes_empty_file_and_reports_error(self):
        target_dir = ROOT / ".test_tmp" / f"backup-failure-{uuid4().hex}"
        target_dir.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, target_dir, True)

        with (
            patch("src.core.database.backup.backup_dir", return_value=target_dir),
            patch("src.core.database.backup.shutil.which", side_effect=[None, "docker"]),
            patch("pathlib.Path.unlink") as unlink,
            patch(
                "src.core.database.backup.subprocess.run",
                side_effect=subprocess.CalledProcessError(
                    1,
                    ["docker", "exec"],
                    stderr=b"No such container: configured-name",
                ),
            ),
            self.assertRaisesRegex(RuntimeError, "No such container"),
        ):
            create_database_backup()

        unlink.assert_called_once_with(missing_ok=True)

    def test_local_state_report_exposes_rows_deleted_by_source_prune(self):
        report = LocalStateMigrationReport(
            ROOT,
            "default",
            1,
            503,
            7,
            1611,
            ROOT / "state.db",
            source_sessions=3,
            source_messages=533,
            source_memories=9,
            source_events=1735,
        )

        self.assertEqual(
            {"sessions": 2, "messages": 30, "memories": 2, "events": 124},
            report.deleted_counts,
        )

    def test_local_source_prune_rolls_back_when_validation_fails(self):
        cursor = FakeCursor([("workspace-id", "session-id")])
        connection = FakeConnection(cursor)
        report = LocalStateMigrationReport(
            ROOT,
            "default",
            1,
            2,
            3,
            4,
            ROOT / "state.db",
        )

        class FailingPrune(LocalStateMigration):
            def _validate_source_prune(self, cur, expected):
                raise RuntimeError("prune validation failed")

        migration = FailingPrune(lambda: connection)
        with self.assertRaisesRegex(RuntimeError, "prune validation failed"):
            migration._prune_source(report)

        self.assertEqual([RuntimeError], connection.transaction_errors)
        statements = " ".join(query for query, _params in cursor.queries).upper()
        self.assertIn("DELETE FROM AGENT_SESSIONS", statements)
        self.assertIn("DELETE FROM AGENT_WORKSPACES", statements)

    def test_local_state_apply_uses_unique_temporary_file_in_target_directory(self):
        target = ROOT / "state.review-test.db"

        class SuccessfulMigration(LocalStateMigration):
            def inspect(self, workspace, keep_session):
                return LocalStateMigrationReport(
                    ROOT,
                    "default",
                    1,
                    0,
                    0,
                    0,
                    target,
                )

            def _copy(self, report, database):
                self.temporary_path = database.path

            def _validate(self, report, database):
                return None

        class FakeLocalDatabase:
            def __init__(self, path):
                self.path = Path(path)
                self.closed = False

            def initialize(self):
                return None

            def close(self):
                self.closed = True

        migration = SuccessfulMigration(lambda: None, target_path=target)
        with (
            patch(
                "src.core.state.migration.create_database_backup",
                return_value=ROOT / "backup.dump",
            ),
            patch("src.core.state.migration.LocalStateDatabase", FakeLocalDatabase),
            patch("src.core.state.migration.os.replace") as replace,
        ):
            report = migration.apply(ROOT)

        self.assertEqual(target.parent, migration.temporary_path.parent)
        self.assertNotEqual(target.with_suffix(".migration.tmp"), migration.temporary_path)
        self.assertIn(".migration.tmp", migration.temporary_path.name)
        replace.assert_called_once_with(migration.temporary_path, target)
        self.assertTrue(report.applied)


if __name__ == "__main__":
    unittest.main()
