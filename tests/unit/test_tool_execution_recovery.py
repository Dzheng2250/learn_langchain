"""Regression tests for checkpoint-safe tool batches and durable recovery."""

import tempfile
import unittest
from pathlib import Path

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from src.core.adapters.sqlite.resource_activity import SQLiteResourceActivityRepository
from src.core.adapters.sqlite.conversation_history import SQLiteConversationHistoryStore
from src.core.adapters.sqlite.session_lifecycle import SQLiteSessionLifecycleStore
from src.core.agent.budget import (
    ExecutionBudget,
    ToolBudgetExceeded,
    bind_execution_budget,
    reset_execution_budget,
)
from src.core.resource_activity.models import (
    ChangeState,
    ObservationMode,
    ResourceObservation,
    ResourceOperation,
)
from src.core.resource_activity.observation import file_snapshot, workspace_uri
from src.core.state import (
    ExecutionRepository,
    ArtifactStore,
    LocalStateDatabase,
    LocalWorkspaceRepository,
    ToolLedgerRepository,
    ToolRecoveryRequired,
)
from src.core.state.types import ExecutionStatus
from src.core.tasks.context import ToolExecutionContext
from src.core.tools.catalog import (
    ApprovalRequirement,
    ToolAudience,
    ToolCapability,
    ToolEffect,
    ToolReplayPolicy,
    ToolRisk,
    ToolSpec,
)
from src.core.tools.observed import LedgerBackedToolNode
from src.core.tools.recovery_service import ToolRecoveryService
from src.core.tools.security.models import PolicyAction, PolicyDecision, ToolCallContext
from src.core.tools.security.pipeline import ToolExecutionPipeline


class AllowPolicy:
    def evaluate(self, context, *, rule_key="", persistable=False):
        return PolicyDecision(PolicyAction.ALLOW)


class ToolExecutionRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.database = LocalStateDatabase(":memory:")
        self.database.initialize()
        self.addCleanup(self.database.close)
        workspace_store = LocalWorkspaceRepository(self.database)
        workspace = workspace_store.resolve(str(self.root))
        self.session, _created = workspace_store.resolve_session(workspace, "recovery")
        self.executions = ExecutionRepository(self.database)
        self.execution = self.executions.begin(self.session, "test")

    def test_side_effect_batch_replays_ledger_results_after_budget_pause(self):
        completed = []

        @tool
        def mutate(value: int) -> str:
            """Record one deterministic mutation."""
            completed.append(value)
            return f"done:{value}"

        spec = ToolSpec(
            name="mutate",
            tool=mutate,
            audiences=frozenset({ToolAudience.PARENT}),
            risk=ToolRisk.CONTROLLED_EXECUTION,
            capabilities=frozenset({ToolCapability.INTERNAL_STATE}),
            approval=ApprovalRequirement.NONE,
            effect=ToolEffect.INTERNAL_MUTATION,
            replay_policy=ToolReplayPolicy.MANUAL,
        )
        ledger = ToolLedgerRepository(self.database)
        pipeline = ToolExecutionPipeline(
            {"mutate": spec},
            policy=AllowPolicy(),
            approvals=None,
            tool_ledger=ledger,
        )
        builder = StateGraph(MessagesState, context_schema=ToolExecutionContext)
        builder.add_node(
            "tools",
            LedgerBackedToolNode(
                [mutate],
                specs={"mutate": spec},
                pipeline=pipeline,
            ),
        )
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "batch"}, "recursion_limit": 100}
        runtime_context = ToolExecutionContext(
            workspace_id=str(self.session.workspace.workspace_id),
            session_id=str(self.session.session_id),
            execution_id=self.execution.execution_id,
            run_id="run-1",
            workspace_root=str(self.root),
            turn_index=1,
            slice_id="slice-1",
        )
        request = AIMessage(
            content="",
            tool_calls=[
                {"name": "mutate", "args": {"value": value}, "id": f"call-{value}"}
                for value in range(13)
            ],
        )
        first_budget = ExecutionBudget(
            max_controlled_executions=12,
            controlled_execution_limit_enabled=True,
            hard_max_tool_calls=20,
            max_wall_seconds=10,
        )
        token = bind_execution_budget(first_budget)
        try:
            with self.assertRaises(ToolBudgetExceeded):
                graph.invoke(
                    {"messages": [request]},
                    config=config,
                    context=runtime_context,
                )
        finally:
            reset_execution_budget(token)

        self.assertEqual(list(range(12)), completed)
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT tool_call_id,status FROM tool_ledger ORDER BY tool_call_id"
            ).fetchall()
        self.assertEqual(12, len(rows))
        self.assertTrue(all(row["status"] == "succeeded" for row in rows))
        checkpoint_messages = graph.get_state(config).values["messages"]
        self.assertFalse(any(isinstance(item, ToolMessage) for item in checkpoint_messages))

        second_budget = ExecutionBudget(
            max_controlled_executions=12,
            controlled_execution_limit_enabled=True,
            hard_max_tool_calls=20,
            max_wall_seconds=10,
        )
        token = bind_execution_budget(second_budget)
        try:
            result = graph.invoke(None, config=config, context=runtime_context)
        finally:
            reset_execution_budget(token)
        self.assertEqual(list(range(13)), completed)
        tool_results = [
            message for message in result["messages"] if isinstance(message, ToolMessage)
        ]
        self.assertEqual(13, len(tool_results))
        self.assertEqual(
            [f"done:{value}" for value in range(13)],
            [message.content for message in tool_results],
        )

    def test_completed_result_is_replayed_without_reexecution(self):
        context = self._context(ToolReplayPolicy.MANUAL)
        ledger = ToolLedgerRepository(self.database)
        self.assertEqual("execute", ledger.claim(context).action)
        expected = ToolMessage(
            content="exact result",
            name=context.tool_name,
            tool_call_id=context.tool_call_id,
        )
        ledger.finish(context, expected)

        replay = ledger.claim(context)
        self.assertEqual("replay", replay.action)
        self.assertEqual("exact result", replay.message.content)

    def test_completed_batch_result_bypasses_policy_and_new_grant_budget(self):
        calls = []

        @tool
        def mutate(value: int) -> str:
            """Record a mutation that must not run during replay."""
            calls.append(value)
            return f"unexpected:{value}"

        spec = ToolSpec(
            name="mutate",
            tool=mutate,
            audiences=frozenset({ToolAudience.PARENT}),
            risk=ToolRisk.CONTROLLED_EXECUTION,
            capabilities=frozenset({ToolCapability.INTERNAL_STATE}),
            approval=ApprovalRequirement.NONE,
            effect=ToolEffect.INTERNAL_MUTATION,
            replay_policy=ToolReplayPolicy.MANUAL,
        )
        ledger_context = ToolCallContext(
            "mutate",
            "completed-call",
            {"value": 7},
            str(self.session.workspace.workspace_id),
            str(self.session.session_id),
            self.execution.execution_id,
            "run-1",
            "parent",
            spec,
            str(self.root),
            1,
            "slice-1",
        )
        ledger = ToolLedgerRepository(self.database)
        ledger.claim(ledger_context)
        ledger.finish(
            ledger_context,
            ToolMessage(
                content="saved:7",
                name="mutate",
                tool_call_id="completed-call",
            ),
        )

        class PolicyMustNotRun:
            def evaluate(self, *args, **kwargs):
                raise AssertionError("completed calls must bypass policy")

        pipeline = ToolExecutionPipeline(
            {"mutate": spec},
            policy=PolicyMustNotRun(),
            approvals=None,
            tool_ledger=ledger,
        )
        builder = StateGraph(MessagesState, context_schema=ToolExecutionContext)
        builder.add_node(
            "tools",
            LedgerBackedToolNode(
                [mutate],
                specs={"mutate": spec},
                pipeline=pipeline,
            ),
        )
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile()
        budget = ExecutionBudget(
            max_controlled_executions=0,
            controlled_execution_limit_enabled=True,
            hard_max_tool_calls=0,
            max_wall_seconds=10,
        )
        token = bind_execution_budget(budget)
        try:
            result = graph.invoke(
                {"messages": [AIMessage(content="", tool_calls=[{
                    "name": "mutate",
                    "args": {"value": 7},
                    "id": "completed-call",
                }])]},
                context=ToolExecutionContext(
                    workspace_id=str(self.session.workspace.workspace_id),
                    session_id=str(self.session.session_id),
                    execution_id=self.execution.execution_id,
                    run_id="run-1",
                    workspace_root=str(self.root),
                    turn_index=1,
                    slice_id="slice-2",
                ),
            )
        finally:
            reset_execution_budget(token)

        self.assertEqual([], calls)
        self.assertEqual(0, budget.tool_calls)
        self.assertEqual("saved:7", result["messages"][-1].content)

    def test_large_completed_result_is_replayed_from_artifact(self):
        context = self._context(ToolReplayPolicy.MANUAL)
        artifacts = ArtifactStore(self.database, root=self.root / "artifacts")
        ledger = ToolLedgerRepository(
            self.database,
            artifact_store=artifacts,
            inline_result_bytes=1024,
        )
        ledger.claim(context)
        content = "x" * 5000
        ledger.finish(
            context,
            ToolMessage(
                content=content,
                name=context.tool_name,
                tool_call_id=context.tool_call_id,
            ),
        )
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT result_payload,artifact_id FROM tool_ledger"
            ).fetchone()
        self.assertEqual("", row["result_payload"])
        self.assertTrue(row["artifact_id"])
        self.assertEqual(content, ledger.claim(context).message.content)

    def test_legacy_workspace_activity_recovers_applied_write(self):
        target = self.root / "result.txt"
        target.write_text("already applied", encoding="utf-8")
        context = self._context(
            ToolReplayPolicy.RECONCILE,
            args={"path": "result.txt", "content": "already applied"},
        )
        snapshot = file_snapshot(target)
        SQLiteResourceActivityRepository(self.database).record(
            context,
            ResourceObservation(
                resource_uri=workspace_uri(self.root, target),
                operation=ResourceOperation.WRITE,
                observation_mode=ObservationMode.EXACT,
                change_state=ChangeState.APPLIED,
                resource_bytes=snapshot["bytes"],
                after_digest=snapshot["digest"],
            ),
        )

        claim = ToolLedgerRepository(self.database).claim(context)
        self.assertEqual("replay", claim.action)
        self.assertEqual("recovered", claim.message.additional_kwargs["tool_execution_status"])

    def test_unknown_side_effect_requires_manual_recovery(self):
        context = self._context(ToolReplayPolicy.MANUAL)
        ledger = ToolLedgerRepository(self.database)
        self.assertEqual("execute", ledger.claim(context).action)
        ledger.mark_running_uncertain()
        with self.assertRaises(ToolRecoveryRequired):
            ledger.claim(context)

    def test_budget_rejection_does_not_leave_a_running_ledger_claim(self):
        context = self._context(ToolReplayPolicy.MANUAL)
        ledger = ToolLedgerRepository(self.database)
        pipeline = ToolExecutionPipeline(
            {},
            policy=None,
            approvals=None,
            tool_ledger=ledger,
        )
        budget = ExecutionBudget(
            max_controlled_executions=0,
            controlled_execution_limit_enabled=True,
            hard_max_tool_calls=10,
            max_wall_seconds=10,
        )
        token = bind_execution_budget(budget)
        try:
            with self.assertRaises(ToolBudgetExceeded):
                pipeline._execute(context, object(), lambda _request: None)
        finally:
            reset_execution_budget(token)

        with self.database.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM tool_ledger").fetchone()[0]
        self.assertEqual(0, count)

    def test_recovery_service_exposes_safe_metadata_and_resolves_error(self):
        context = self._context(ToolReplayPolicy.MANUAL)
        ledger = ToolLedgerRepository(self.database)
        ledger.claim(context)
        ledger.mark_running_uncertain()
        self.executions.pause(
            self.execution.execution_id,
            ExecutionStatus.PAUSED_RECOVERY,
            "tool_recovery_required",
        )
        service = ToolRecoveryService(
            repository=ledger,
            session_store=SQLiteSessionLifecycleStore(
                workspace_repository=LocalWorkspaceRepository(self.database),
                history_store=SQLiteConversationHistoryStore(self.database),
            ),
            execution_repository=self.executions,
        )

        listed = service.list_pending(str(self.root), "recovery")
        item = service.get(str(self.root), "recovery", context.tool_call_id)["item"]
        self.assertEqual(1, listed["count"])
        self.assertNotIn("result_payload", item)
        self.assertNotIn("before_state", item)
        response = service.prepare_response(
            str(self.root),
            "recovery",
            context.tool_call_id,
            "return_error",
        )
        self.assertEqual("tool_recovery", response["type"])
        self.assertEqual("replay", ledger.claim(context).action)

    def test_repeated_pause_fingerprint_requires_condition_retry(self):
        fingerprint = "budget_limit:call-13"
        self.executions.pause(
            self.execution.execution_id,
            ExecutionStatus.PAUSED_BUDGET,
            "budget_limit",
            pause_fingerprint=fingerprint,
        )
        self.executions.resume(self.session)
        self.executions.pause(
            self.execution.execution_id,
            ExecutionStatus.PAUSED_BUDGET,
            "budget_limit",
            pause_fingerprint=fingerprint,
        )
        pending = self.executions.get_pending(self.session)
        self.assertEqual(ExecutionStatus.PAUSED_RECOVERY, pending.status)
        self.assertEqual("condition_required", pending.resume_policy)
        with self.assertRaises(ValueError):
            self.executions.resume(self.session)

    def _context(self, replay_policy, *, args=None):
        @tool
        def write_file(path: str, content: str = "") -> str:
            """Write one test file."""
            return content

        spec = ToolSpec(
            name="write_file",
            tool=write_file,
            audiences=frozenset({ToolAudience.PARENT}),
            risk=ToolRisk.CONTROLLED_EXECUTION,
            capabilities=frozenset({ToolCapability.FILE_WRITE}),
            approval=ApprovalRequirement.POLICY,
            effect=ToolEffect.WORKSPACE_MUTATION,
            replay_policy=replay_policy,
        )
        return ToolCallContext(
            "write_file",
            "call-1",
            dict(args or {"path": "result.txt", "content": "value"}),
            str(self.session.workspace.workspace_id),
            str(self.session.session_id),
            self.execution.execution_id,
            "run-1",
            "parent",
            spec,
            str(self.root),
            1,
            "slice-1",
        )


if __name__ == "__main__":
    unittest.main()
