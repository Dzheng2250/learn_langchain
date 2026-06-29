"""Unit tests for tool registration, hooks, policy, and approval persistence."""

import unittest
from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command

from src.core.adapters.sqlite.tool_approvals import SQLiteToolApprovalRepository
from src.core.state import ExecutionRepository, LocalStateDatabase, LocalWorkspaceRepository
from src.core.tools.catalog import (
    ApprovalRequirement, NetworkMode, SandboxMode, ToolAudience,
    ToolCapability, ToolRegistry, ToolRisk, ToolSpec,
)
from src.core.tools.security.approval import ApprovalService
from src.core.tools.security.command_rules import command_rule_key
from src.core.tools.security.hooks import HookRunner
from src.core.tools.security.models import (
    ApprovalResponse, HookAction, HookDecision, PolicyAction, ToolCallContext,
)
from src.core.tools.security.policy import DefaultToolPolicyEngine
from src.core.tools.security.pipeline import ToolExecutionPipeline
from src.core.tools.observed import ObservedToolNode
from src.core.streaming.events import stream_graph_events
from src.core.tasks.context import ToolExecutionContext


class _Tool:
    name = "command"


def _spec(**overrides):
    values = {
        "name": "command",
        "tool": _Tool(),
        "audiences": frozenset({ToolAudience.PARENT}),
        "risk": ToolRisk.CONTROLLED_EXECUTION,
        "capabilities": frozenset({ToolCapability.COMMAND_EXECUTION}),
        "approval": ApprovalRequirement.POLICY,
    }
    values.update(overrides)
    return ToolSpec(**values)


class ToolSecurityTest(unittest.TestCase):
    def setUp(self):
        self.database = LocalStateDatabase(":memory:")
        self.database.initialize()
        self.addCleanup(self.database.close)
        self.workspaces = LocalWorkspaceRepository(self.database)
        workspace = self.workspaces.resolve(str(Path("tests/fixtures/workspace_a").resolve()))
        session, _ = self.workspaces.resolve_session(workspace, "security")
        self.session = session
        execution = ExecutionRepository(self.database).begin(session, "test")
        self.context = ToolCallContext(
            "command", "call-1", {"command": "python -m unittest"},
            str(workspace.workspace_id), str(session.session_id),
            execution.execution_id, "run-1", "parent", _spec(), str(workspace.root),
        )
        self.repository = SQLiteToolApprovalRepository(self.database)

    def test_invalid_tool_metadata_is_rejected(self):
        with self.assertRaises(ValueError):
            _spec(
                capabilities=frozenset({ToolCapability.NETWORK_ACCESS}),
                network=NetworkMode.DENY,
            )
        with self.assertRaises(ValueError):
            _spec(
                sandbox=SandboxMode.HOST_FULL_ACCESS,
                approval=ApprovalRequirement.POLICY,
            )

    def test_factory_backed_tool_is_created_once(self):
        created = []

        def factory():
            created.append(True)
            return _Tool()

        registry = ToolRegistry()
        registry.register(_spec(tool=None, factory=factory))
        registry.freeze()
        self.assertIs(registry.tools_for(ToolAudience.PARENT)[0], registry.tools_for(ToolAudience.PARENT)[0])
        self.assertEqual(1, len(created))

    def test_failing_pre_hook_rejects_call(self):
        class BrokenHook:
            def before_tool(self, _context):
                raise RuntimeError("private")

        _context, decision = HookRunner([BrokenHook()]).before(self.context)
        self.assertEqual(HookAction.REJECT, decision.action)
        self.assertNotIn("private", decision.reason)

    def test_scoped_deny_rule_has_priority(self):
        policy = DefaultToolPolicyEngine(self.repository)
        initial = policy.evaluate(self.context, rule_key="tool:command", persistable=True)
        self.assertEqual(PolicyAction.ASK, initial.action)
        request = ApprovalService(self.repository).request(self.context, initial)
        self.repository.apply_response(
            request["request_id"], ApprovalResponse.DENY_SESSION,
            context=self.context, rule_key="tool:command", persistable=True,
        )
        denied = policy.evaluate(self.context, rule_key="tool:command", persistable=True)
        self.assertEqual(PolicyAction.DENY, denied.action)

    def test_hard_delete_removes_session_scoped_rules(self):
        decision = DefaultToolPolicyEngine(self.repository).evaluate(
            self.context, rule_key="tool:command", persistable=True
        )
        request = ApprovalService(self.repository).request(self.context, decision)
        self.repository.apply_response(
            request["request_id"], ApprovalResponse.DENY_SESSION,
            context=self.context, rule_key="tool:command", persistable=True,
        )
        self.assertTrue(self.workspaces.delete_session(self.session))
        with self.database.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM tool_permission_rules WHERE session_id = ?",
                (str(self.session.session_id),),
            ).fetchone()[0]
        self.assertEqual(0, count)

    def test_approval_summary_redacts_secret_and_is_single_use(self):
        context = self.context.with_args({
            "command": "curl -H Authorization=secret --token hidden",
            "api_key": "secret",
        })
        decision = DefaultToolPolicyEngine(self.repository).evaluate(
            context, rule_key="tool:command", persistable=True
        )
        request = ApprovalService(self.repository).request(context, decision)
        self.assertEqual("<redacted>", request["args"]["api_key"])
        self.assertNotIn("secret", request["args"]["command"])
        self.assertNotIn("hidden", request["args"]["command"])
        self.repository.apply_response(
            request["request_id"], ApprovalResponse.ALLOW_ONCE,
            context=context, rule_key="tool:command", persistable=True,
        )
        with self.assertRaises(ValueError):
            self.repository.apply_response(
                request["request_id"], ApprovalResponse.ALLOW_ONCE,
                context=context, rule_key="tool:command", persistable=True,
            )

    def test_compound_shell_rule_is_not_persistable(self):
        _key, persistable = command_rule_key("echo ok | grep ok")
        self.assertFalse(persistable)

    def test_simple_argv_rule_matches_longer_command_prefix(self):
        rule_key, persistable = command_rule_key("python -m unittest")
        self.assertTrue(persistable)
        decision = DefaultToolPolicyEngine(self.repository).evaluate(
            self.context, rule_key=rule_key, persistable=True
        )
        request = ApprovalService(self.repository).request(self.context, decision)
        self.repository.apply_response(
            request["request_id"], ApprovalResponse.ALLOW_SESSION,
            context=self.context, rule_key=rule_key, persistable=True,
        )
        expanded_key, expanded_persistable = command_rule_key(
            "python -m unittest discover -s tests"
        )
        allowed = DefaultToolPolicyEngine(self.repository).evaluate(
            self.context, rule_key=expanded_key, persistable=expanded_persistable
        )
        self.assertEqual(PolicyAction.ALLOW, allowed.action)

    def test_langgraph_interrupt_resumes_the_same_tool_call(self):
        calls = []

        @tool
        def command(command: str) -> str:
            """Record one approved test command."""
            calls.append(command)
            return "ok"

        spec = _spec(tool=command)
        pipeline = ToolExecutionPipeline(
            {"command": spec},
            policy=DefaultToolPolicyEngine(self.repository),
            approvals=ApprovalService(self.repository),
        )
        builder = StateGraph(MessagesState, context_schema=ToolExecutionContext)
        builder.add_node("tools", ObservedToolNode([command], pipeline=pipeline))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(checkpointer=MemorySaver())
        tool_context = ToolExecutionContext(
            self.context.workspace_id,
            self.context.session_id,
            self.context.execution_id,
            self.context.run_id,
            "parent",
            self.context.workspace_root,
        )
        first = list(
            stream_graph_events(
                graph,
                [AIMessage(content="", tool_calls=[{
                    "name": "command",
                    "args": {"command": "python -V"},
                    "id": "approval-call",
                    "type": "tool_call",
                }])],
                checkpoint_thread_id="approval-thread",
                tool_context=tool_context,
            )
        )
        required = next(item for item in first if item["event"] == "tool_approval_required")
        self.assertEqual([], calls)
        second = list(
            stream_graph_events(
                graph,
                Command(resume={
                    "request_id": required["data"]["request_id"],
                    "response": "allow_once",
                }),
                checkpoint_thread_id="approval-thread",
                tool_context=tool_context,
            )
        )
        self.assertEqual(["python -V"], calls)
        self.assertEqual("done", second[-1]["event"])


if __name__ == "__main__":
    unittest.main()
