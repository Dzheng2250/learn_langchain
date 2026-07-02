"""Contract tests for the durable frontend-neutral resource activity ledger."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from src.core.resource_activity import (
    ChangeState, ObservationMode, ResourceObservation, ResourceOperation,
)
from src.core.adapters.sqlite.resource_activity import SQLiteResourceActivityRepository
from src.core.resource_activity.observation import command_uri, file_snapshot, workspace_uri
from src.core.state.migrations import apply_local_migrations, downgrade_v11_to_v10
from src.core.state import ExecutionRepository, LocalStateDatabase, LocalWorkspaceRepository

class ResourceActivityRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.root=Path.cwd()
        self.database=LocalStateDatabase(":memory:"); self.database.initialize()
        self.workspaces=LocalWorkspaceRepository(self.database)
        workspace=self.workspaces.resolve(str(self.root))
        self.session,_=self.workspaces.resolve_session(workspace,"default")
        self.execution=ExecutionRepository(self.database).begin(self.session,"test")
        self.context=SimpleNamespace(
            workspace_id=str(workspace.workspace_id),session_id=str(self.session.session_id),
            turn_index=1,execution_id=self.execution.execution_id,slice_id=None,run_id="run-1",
            tool_call_id="call-1",tool_name="read_workspace_file_lite",actor="parent",
            workspace_root=str(self.root),
        )
        self.repo=SQLiteResourceActivityRepository(self.database,max_items=10)
    def tearDown(self): self.database.close()

    def test_records_read_and_links_current_write_evidence(self):
        read_id=self.repo.record(self.context,ResourceObservation(
            "workspace://a.py",ResourceOperation.READ,ObservationMode.EXACT,
            returned_bytes=20,resource_bytes=20,after_digest="same",
        ))
        write_id=self.repo.record(self.context,ResourceObservation(
            "workspace://a.py",ResourceOperation.WRITE,ObservationMode.EXACT,
            change_state=ChangeState.APPLIED,before_digest="same",after_digest="next",resource_bytes=21,
        ))
        result=self.repo.list(execution_id=self.execution.execution_id)
        self.assertEqual([read_id,write_id],[item["activity_id"] for item in result["items"]])
        self.assertEqual("current",result["items"][1]["evidence_status"])
        self.assertEqual([read_id],result["items"][1]["related_activity_ids"])
        summary=self.repo.summary(execution_id=self.execution.execution_id).to_dict()
        self.assertEqual(1,summary["reads"]["resource_count"])
        self.assertEqual(20,summary["reads"]["returned_bytes"])
        self.assertEqual(1,summary["changes"]["applied"])

    def test_partial_stale_missing_and_pagination_are_stable(self):
        self.repo.record(self.context,ResourceObservation("workspace://partial.py",ResourceOperation.READ,ObservationMode.RANGE,after_digest="a"))
        self.repo.record(self.context,ResourceObservation("workspace://partial.py",ResourceOperation.WRITE,ObservationMode.EXACT,change_state=ChangeState.APPLIED,before_digest="a"))
        self.repo.record(self.context,ResourceObservation("workspace://stale.py",ResourceOperation.READ,ObservationMode.EXACT,after_digest="old"))
        self.repo.record(self.context,ResourceObservation("workspace://stale.py",ResourceOperation.WRITE,ObservationMode.EXACT,change_state=ChangeState.APPLIED,before_digest="new"))
        self.repo.record(self.context,ResourceObservation("workspace://missing.py",ResourceOperation.DELETE,ObservationMode.EXACT,change_state=ChangeState.APPLIED))
        first=self.repo.list(execution_id=self.execution.execution_id,limit=2)
        second=self.repo.list(execution_id=self.execution.execution_id,cursor=first["next_cursor"],limit=10)
        self.assertTrue(first["has_more"]); self.assertFalse(second["has_more"])
        statuses=[item["evidence_status"] for item in first["items"]+second["items"] if item["operation"] in {"write","delete"}]
        self.assertEqual(["partial","stale","missing"],statuses)

    def test_uri_normalization_idempotency_and_terminal_change_state(self):
        read_id = self.repo.record(self.context, ResourceObservation(
            workspace_uri(self.root, "./a.py"), ResourceOperation.READ,
            ObservationMode.EXACT, after_digest="same", event_key="read:0",
        ))
        self.assertEqual(
            "current",
            self.repo.evidence_for(SimpleNamespace(
                args={"path": "./a.py"}, execution_id=self.execution.execution_id,
                workspace_root=str(self.root),
            ))["status"],
        )
        duplicate = self.repo.record(self.context, ResourceObservation(
            "workspace://a.py", ResourceOperation.READ, ObservationMode.EXACT,
            after_digest="same", event_key="read:0",
        ))
        self.assertEqual(read_id, duplicate)
        self.repo.record(self.context, ResourceObservation(
            "workspace://a.py", ResourceOperation.WRITE, ObservationMode.EXACT,
            change_state=ChangeState.PROPOSED, metadata={"change_set_id": "set-1"},
            event_key="stage:0",
        ))
        self.repo.record(self.context, ResourceObservation(
            "workspace://a.py", ResourceOperation.WRITE, ObservationMode.EXACT,
            change_state=ChangeState.APPLIED, metadata={"change_set_id": "set-1"},
            event_key="apply:0",
        ))
        summary = self.repo.summary(execution_id=self.execution.execution_id).to_dict()
        self.assertEqual(0, summary["changes"]["proposed"])
        self.assertEqual(1, summary["changes"]["applied"])

    def test_command_uri_is_distinct_and_does_not_expose_command_text(self):
        first = command_uri("docker", "pytest tests/unit")
        second = command_uri("docker", "python -V")
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("command://docker/"))
        self.assertNotIn("pytest", first)
    def test_file_snapshot_counts_trailing_newline_correctly(self):
        path = self.root / ".test_tmp_resource_activity_lines.txt"
        path.write_bytes(b"a\nb\n")
        self.addCleanup(path.unlink, missing_ok=True)
        self.assertEqual(2, file_snapshot(path)["lines"])
    def test_concurrent_duplicate_event_key_returns_the_existing_activity(self):
        observation = ResourceObservation(
            "workspace://same.py", ResourceOperation.READ, ObservationMode.EXACT,
            event_key="same-event",
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            ids = list(executor.map(
                lambda _index: self.repo.record(self.context, observation), range(2)
            ))
        self.assertEqual(ids[0], ids[1])
        result = self.repo.list(execution_id=self.execution.execution_id)
        self.assertEqual(1, len(result["items"]))
    def test_move_pair_is_one_applied_change_and_destination_is_queryable(self):
        group = "move-1"
        self.repo.record(self.context, ResourceObservation(
            "workspace://source.py", ResourceOperation.MOVE, ObservationMode.EXACT,
            change_state=ChangeState.APPLIED,
            metadata={"change_group_id": group, "move_role": "source"},
            event_key="move:source",
        ))
        self.repo.record(self.context, ResourceObservation(
            "workspace://destination.py", ResourceOperation.MOVE, ObservationMode.EXACT,
            change_state=ChangeState.APPLIED,
            metadata={"change_group_id": group, "move_role": "destination"},
            event_key="move:destination",
        ))
        summary = self.repo.summary(execution_id=self.execution.execution_id).to_dict()
        destination = self.repo.list(
            execution_id=self.execution.execution_id,
            resource_uri="workspace://destination.py",
        )
        self.assertEqual(1, summary["changes"]["applied"])
        self.assertEqual(1, summary["changes"]["changed_resource_count"])
        self.assertEqual(1, len(destination["items"]))

    def test_summary_ignores_malformed_metadata_without_losing_other_rows(self):
        activity_id = self.repo.record(self.context, ResourceObservation(
            "workspace://bad.py", ResourceOperation.WRITE, ObservationMode.EXACT,
            change_state=ChangeState.APPLIED, event_key="bad-metadata",
        ))
        with self.database.transaction() as conn:
            conn.execute(
                "UPDATE resource_activities SET metadata=? WHERE activity_id=?",
                ("{broken", activity_id),
            )
        summary = self.repo.summary(execution_id=self.execution.execution_id).to_dict()
        self.assertEqual(1, summary["changes"]["applied"])

    def test_workspace_uri_rejects_absolute_paths_outside_workspace(self):
        outside = self.root.parent / "outside-resource.txt"
        with self.assertRaisesRegex(ValueError, "escapes the workspace"):
            workspace_uri(self.root, outside)

    def test_core_cli_can_apply_supported_v11_rollback_with_backup(self):
        from src.core.main import main

        directory = self.root / ".test_tmp" / f"rollback-{uuid4().hex}"
        directory.mkdir(parents=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        database = LocalStateDatabase(directory / "state.db")
        database.initialize()
        self.addCleanup(database.close)
        config = SimpleNamespace(runtime_dir=directory)
        with (
            patch("src.core.main.LocalStateDatabase", return_value=database),
            patch("src.core.main.CoreConfig.load", return_value=config),
            patch("src.core.main.daemon_pid_is_running", return_value=False),
        ):
            self.assertEqual(0, main([
                "rollback-local-state", "--from-version", "11",
                "--to-version", "10", "--apply",
            ]))
        with database.connect() as conn:
            version = conn.execute(
                "SELECT MAX(version) FROM local_schema_migrations"
            ).fetchone()[0]
        self.assertEqual(10, version)
        self.assertEqual(1, len(list(directory.glob("state.db.v11-backup-*"))))
    def test_v11_repair_adds_event_key_to_an_early_v11_database(self):
        with self.database.transaction() as conn:
            conn.execute("DROP INDEX IF EXISTS idx_resource_activities_event_key")
            conn.execute("ALTER TABLE resource_activities DROP COLUMN event_key")
            apply_local_migrations(conn)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(resource_activities)")}
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(resource_activities)")}
        self.assertIn("event_key", columns)
        self.assertIn("idx_resource_activities_event_key", indexes)
    def test_v11_downgrade_removes_only_resource_activity_tables(self):
        with self.database.transaction() as conn:
            downgrade_v11_to_v10(conn)
        with self.database.connect() as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            version = conn.execute("SELECT max(version) FROM local_schema_migrations").fetchone()[0]
        self.assertNotIn("resource_activities", tables)
        self.assertNotIn("resource_activity_counters", tables)
        self.assertEqual(10, version)
    def test_limit_marks_summary_truncated_and_session_delete_cascades(self):
        repo=SQLiteResourceActivityRepository(self.database,max_items=1)
        observation=ResourceObservation("workspace://a",ResourceOperation.READ,ObservationMode.EXACT)
        self.assertIsNotNone(repo.record(self.context,observation)); self.assertIsNone(repo.record(self.context,observation))
        self.assertTrue(repo.summary(execution_id=self.execution.execution_id).truncated)
        self.assertTrue(self.workspaces.delete_session(self.session))
        with self.database.connect() as conn:
            count=conn.execute("SELECT count(*) FROM resource_activities").fetchone()[0]
        self.assertEqual(0,count)

if __name__ == "__main__": unittest.main()
