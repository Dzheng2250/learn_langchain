"""Contract tests for pluggable tool-approval modes."""

import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.config.settings import _load_tool_approval_mode
from src.core.adapters.sqlite.tool_approvals import SQLiteToolApprovalRepository
from src.core.adapters.sqlite.session_lifecycle import SQLiteSessionLifecycleStore
from src.core.state import ExecutionRepository, LocalStateDatabase, LocalWorkspaceRepository
from src.core.state.migrations import (
    apply_local_migrations,
    downgrade_v12_to_v11,
)
from src.core.tools.approval_service import ToolApprovalService
from src.core.tools.security.approval import ApprovalService
from src.core.tools.security.models import PolicyAction, PolicyDecision
from src.core.tools.security.modes import (
    AcceptAllApprovalStrategy,
    ApprovalModeResolver,
    ApprovalStrategyRegistry,
    ManualApprovalStrategy,
)


class ToolApprovalModeTest(unittest.TestCase):
    def test_new_environment_mode_takes_precedence_over_legacy_boolean(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "LEARN_AGENT_TOOL_APPROVAL_MODE": "accept_all",
                "LEARN_AGENT_TOOL_APPROVAL_ENABLED": "true",
            },
            clear=False,
        ):
            self.assertEqual("accept_all", _load_tool_approval_mode())

    def test_legacy_false_maps_to_accept_all_with_deprecation_warning(self) -> None:
        with patch.dict(
            "os.environ",
            {"LEARN_AGENT_TOOL_APPROVAL_ENABLED": "false"},
            clear=False,
        ):
            with patch.dict(
                "os.environ",
                {"LEARN_AGENT_TOOL_APPROVAL_MODE": ""},
                clear=False,
            ):
                # An explicitly empty new setting is still invalid and must not
                # silently activate the legacy bypass.
                self.assertEqual("", _load_tool_approval_mode())
        with patch.dict(
            "os.environ",
            {"LEARN_AGENT_TOOL_APPROVAL_ENABLED": "false"},
            clear=True,
        ):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                self.assertEqual("accept_all", _load_tool_approval_mode())
        self.assertTrue(any(item.category is FutureWarning for item in caught))

    def setUp(self) -> None:
        self.database = LocalStateDatabase(":memory:")
        self.database.initialize()
        self.addCleanup(self.database.close)
        self.workspaces = LocalWorkspaceRepository(self.database)
        self.workspace = self.workspaces.resolve(
            str(Path("tests/fixtures/workspace_a").resolve())
        )
        self.session, _created = self.workspaces.resolve_session(
            self.workspace, "approval-modes"
        )
        self.execution = ExecutionRepository(self.database).begin(self.session, "test")
        self.repository = SQLiteToolApprovalRepository(self.database)
        self.registry = ApprovalStrategyRegistry()
        session_store = SQLiteSessionLifecycleStore(
            workspace_repository=self.workspaces,
            history_store=None,
        )
        self.service = ToolApprovalService(
            repository=self.repository,
            session_store=session_store,
            strategy_registry=self.registry,
            default_mode="manual",
        )

    def test_built_in_strategy_contract(self) -> None:
        decision = PolicyDecision(PolicyAction.ASK, "approval required")

        manual = ManualApprovalStrategy().decide(None, decision)
        automatic = AcceptAllApprovalStrategy().decide(None, decision)

        self.assertEqual("wait", manual.action.value)
        self.assertEqual("auto_allow", automatic.action.value)
        self.assertEqual("allow_once", automatic.response.value)

    def test_session_override_and_inherit_survive_repository_reads(self) -> None:
        root = str(self.workspace.root)
        initial = self.service.get_mode(root, "approval-modes")
        automatic = self.service.set_mode(
            root,
            "approval-modes",
            "accept_all",
            acknowledge_risk=True,
        )
        inherited = self.service.set_mode(root, "approval-modes", "inherit")

        self.assertEqual("manual", initial["effective_mode"])
        self.assertEqual("accept_all", automatic["effective_mode"])
        self.assertEqual("accept_all", automatic["override_mode"])
        self.assertEqual("manual", inherited["effective_mode"])
        self.assertIsNone(inherited["override_mode"])

    def test_accept_all_requires_explicit_risk_acknowledgment(self) -> None:
        with self.assertRaisesRegex(ValueError, "acknowledge_risk"):
            self.service.set_mode(
                str(self.workspace.root),
                "approval-modes",
                "accept_all",
            )

    def test_mode_response_reports_existing_pending_without_resolving_it(self) -> None:
        class Context:
            workspace_id = str(self.workspace.workspace_id)
            session_id = str(self.session.session_id)
            execution_id = self.execution.execution_id
            tool_call_id = "pending-mode-switch"
            tool_name = "command"
            actor = "parent"
            args = {"command": "python -V"}

        decision = PolicyDecision(
            PolicyAction.ASK,
            "approval required",
            rule_key="tool:command",
            persistable=True,
        )
        request = ApprovalService(self.repository).request(Context(), decision)

        result = self.service.set_mode(
            str(self.workspace.root),
            "approval-modes",
            "accept_all",
            acknowledge_risk=True,
        )

        self.assertTrue(result["existing_pending_unchanged"])
        self.assertEqual(1, result["pending_count"])
        self.assertIsNotNone(self.repository.get_pending(request["request_id"]))

    def test_unknown_persisted_value_is_reported_as_manual(self) -> None:
        self.repository.set_session_mode(
            str(self.workspace.workspace_id),
            str(self.session.session_id),
            "not-installed",
        )
        resolver = ApprovalModeResolver(
            self.repository,
            self.registry,
            default_mode="accept_all",
        )

        self.assertEqual("manual", resolver.resolve(type("Context", (), {
            "workspace_id": str(self.workspace.workspace_id),
            "session_id": str(self.session.session_id),
        })()))
        self.assertEqual(
            "manual",
            self.service.get_mode(str(self.workspace.root), "approval-modes")[
                "effective_mode"
            ],
        )

    def test_unknown_global_default_falls_back_to_manual(self) -> None:
        resolver = ApprovalModeResolver(
            self.repository,
            self.registry,
            default_mode="not-installed",
        )
        service = ToolApprovalService(
            repository=self.repository,
            session_store=self.service.session_store,
            strategy_registry=self.registry,
            default_mode="not-installed",
        )

        self.assertEqual("manual", resolver.default_mode)
        self.assertEqual("manual", service.default_mode)

    def test_v12_downgrade_and_upgrade_preserve_existing_rows(self) -> None:
        context = SimpleNamespace(
            workspace_id=str(self.workspace.workspace_id),
            session_id=str(self.session.session_id),
            execution_id=self.execution.execution_id,
            tool_call_id="migration-approval",
            tool_name="command",
            actor="parent",
            args={"command": "python -V"},
        )
        decision = PolicyDecision(
            PolicyAction.ASK,
            "approval required",
            rule_key="tool:command",
            persistable=True,
        )
        request = ApprovalService(self.repository).request(
            context,
            decision,
            approval_mode="manual",
        )
        self.repository.apply_response(
            request["request_id"],
            "allow_session",
            context=context,
            rule_key=decision.rule_key,
            persistable=True,
            decision_source="user",
            approval_mode="manual",
        )
        with self.database.transaction() as conn:
            downgrade_v12_to_v11(conn)
            self.assertEqual(
                11,
                conn.execute(
                    "SELECT MAX(version) FROM local_schema_migrations"
                ).fetchone()[0],
            )
            self.assertNotIn(
                "tool_approval_mode",
                {row[1] for row in conn.execute("PRAGMA table_info(sessions)")},
            )
            apply_local_migrations(conn)
            self.assertEqual(
                12,
                conn.execute(
                    "SELECT MAX(version) FROM local_schema_migrations"
                ).fetchone()[0],
            )
            self.assertIn(
                "tool_approval_mode",
                {row[1] for row in conn.execute("PRAGMA table_info(sessions)")},
            )
            counts = tuple(conn.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM sessions WHERE session_id=?), "
                "(SELECT COUNT(*) FROM tool_approval_requests WHERE request_id=?), "
                "(SELECT COUNT(*) FROM tool_permission_rules WHERE session_id=?), "
                "(SELECT COUNT(*) FROM tool_approval_audit WHERE request_id=?)",
                (
                    str(self.session.session_id),
                    request["request_id"],
                    str(self.session.session_id),
                    request["request_id"],
                ),
            ).fetchone())
            provenance = conn.execute(
                "SELECT decision_source, approval_mode FROM tool_approval_audit "
                "WHERE request_id=?",
                (request["request_id"],),
            ).fetchone()
        self.assertEqual((1, 1, 1, 1), counts)
        self.assertEqual(("legacy", "manual"), tuple(provenance))


if __name__ == "__main__":
    unittest.main()
