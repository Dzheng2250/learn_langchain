import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.database.migration import MigrationReport, WorkspaceMigration
from src.core.database.schema import LegacySchemaError, SchemaManager


ROOT = Path(__file__).resolve().parents[1]


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


if __name__ == "__main__":
    unittest.main()
