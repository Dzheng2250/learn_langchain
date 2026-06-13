import os
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config.maintenance import MaintenanceSettings
from src.core.maintenance import MaintenanceJobSpec, MaintenanceRepository
from src.core.maintenance.types import (
    MaintenanceJobType,
    MaintenancePriority,
    MaintenanceStatus,
)
from src.core.prompts import (
    SUBAGENT_SYSTEM_PROMPT,
    build_context_summary_messages,
    build_parent_system_prompt,
)
from src.core.state import CheckpointState, ExecutionRepository, ExecutionStatus
from src.core.state import LocalStateDatabase
from src.core.state.workspace import LocalWorkspaceRepository


class MaintenanceSettingsTest(unittest.TestCase):
    def test_environment_overrides_are_loaded_into_typed_policy(self):
        with patch.dict(
            os.environ,
            {
                "LEARN_AGENT_MAINTENANCE_POLL_INTERVAL_SECONDS": "0.5",
                "LEARN_AGENT_MAINTENANCE_LEASE_SECONDS": "90",
                "LEARN_AGENT_MAINTENANCE_MAX_ATTEMPTS": "7",
                "LEARN_AGENT_MAINTENANCE_MAX_RETRY_DELAY_SECONDS": "600",
            },
            clear=False,
        ):
            settings = MaintenanceSettings.load()

        self.assertEqual(0.5, settings.poll_interval_seconds)
        self.assertEqual(90, settings.lease_seconds)
        self.assertEqual(7, settings.default_max_attempts)
        self.assertEqual(600, settings.max_retry_delay_seconds)

    def test_invalid_policy_is_rejected_before_services_start(self):
        with self.assertRaisesRegex(ValueError, "poll interval"):
            MaintenanceSettings(poll_interval_seconds=0).validate()

    def test_component_rejects_an_invalid_injected_policy(self):
        database = LocalStateDatabase(":memory:")
        self.addCleanup(database.close)
        database.initialize()
        with self.assertRaisesRegex(ValueError, "max attempts"):
            MaintenanceRepository(
                database,
                MaintenanceSettings(default_max_attempts=0),
            )


class DomainVocabularyTest(unittest.TestCase):
    def setUp(self):
        self.database = LocalStateDatabase(":memory:")
        self.addCleanup(self.database.close)
        self.database.initialize()
        workspaces = LocalWorkspaceRepository(self.database)
        workspace = workspaces.resolve(str(Path("tests/fixtures/workspace_a").resolve()))
        self.session, _ = workspaces.resolve_session(workspace, "typed-domain")

    def test_repository_uses_injected_maintenance_policy(self):
        repository = MaintenanceRepository(
            self.database,
            MaintenanceSettings(default_max_attempts=9),
        )
        repository.enqueue(
            MaintenanceJobSpec(
                MaintenanceJobType.CONTEXT_SUMMARY,
                "typed-policy",
                str(self.session.workspace.workspace_id),
                str(self.session.session_id),
                priority=MaintenancePriority.CONTEXT_SUMMARY,
            )
        )

        job = repository.get_by_dedupe_key("typed-policy")
        self.assertEqual(MaintenanceStatus.PENDING, job.status)
        self.assertEqual(9, job.max_attempts)

    def test_invalid_execution_transition_is_rejected(self):
        execution = ExecutionRepository(self.database).begin(self.session, "task")
        with self.assertRaises(ValueError):
            ExecutionRepository(self.database).pause(
                execution.execution_id,
                "invented_status",
                "invalid",
            )

    def test_fresh_schema_rejects_unknown_persisted_status(self):
        with self.assertRaises(sqlite3.IntegrityError), self.database.transaction() as conn:
            conn.execute(
                """
                INSERT INTO maintenance_jobs(
                    job_id, workspace_id, session_id, job_type, dedupe_key, status
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "invalid-job",
                    str(self.session.workspace.workspace_id),
                    str(self.session.session_id),
                    MaintenanceJobType.CONTEXT_SUMMARY,
                    "invalid-status",
                    "invented_status",
                ),
            )

    def test_domain_enums_remain_wire_and_database_compatible_strings(self):
        self.assertEqual("completed", ExecutionStatus.COMPLETED)
        self.assertEqual("cleanup_pending", CheckpointState.CLEANUP_PENDING)
        self.assertEqual("context_summary", MaintenanceJobType.CONTEXT_SUMMARY)


class PromptBoundaryTest(unittest.TestCase):
    def test_parent_prompt_is_built_from_explicit_runtime_facts(self):
        prompt = build_parent_system_prompt("pdf: read PDFs", 200)
        self.assertIn("at most 200 lines", prompt)
        self.assertIn("pdf: read PDFs", prompt)

    def test_context_prompt_builder_returns_model_messages(self):
        messages = build_context_summary_messages(
            source="conversation",
            previous_summary="old",
            memory_context="memory",
            summary_max_chars=4000,
        )
        self.assertEqual(2, len(messages))
        self.assertIn("Previous summary:\nold", messages[0].content)
        self.assertIn("under 4000 characters", messages[1].content)

    def test_subagent_policy_explicitly_forbids_recursive_delegation(self):
        self.assertIn("cannot delegate", SUBAGENT_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
