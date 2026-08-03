"""Unit tests for tool registration, hooks, policy, and approval persistence."""

import unittest
from dataclasses import replace
from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command

from src.core.adapters.sqlite.resource_activity import SQLiteResourceActivityRepository
from src.core.adapters.sqlite.tool_approvals import SQLiteToolApprovalRepository
from src.core.state import ExecutionRepository, LocalStateDatabase, LocalWorkspaceRepository
from src.core.hooks import (
    HookAction, HookContext, HookDecision, HookDispatcher, HookFailureMode,
    HookPoint, HookRegistry, HookSpec,
)
from src.core.tools.catalog import (
    ApprovalRequirement, NetworkMode, SandboxMode, ToolAudience,
    ToolCapability, ToolRegistry, ToolRisk, ToolSpec,
)
from src.core.tools.security.approval import ApprovalService
from src.core.tools.security.command_rules import command_rule_key
from src.core.tools.security.enforcement import CapabilityEnforcer
from src.core.tools.security.models import (
    ApprovalResponse, ApprovalStrategyAction, ApprovalStrategyDecision,
    PolicyAction, PolicyDecision, ToolCallContext,
)
from src.core.tools.security.modes import (
    ApprovalCoordinator, ApprovalModeResolver, ApprovalStrategyRegistry,
)
from src.core.tools.security.policy import DefaultToolPolicyEngine
from src.core.tools.security.pipeline import ToolExecutionPipeline
from src.core.tools.observed import ObservedToolNode
from src.core.resource_activity import (
    ObservationMode, ResourceObservation, ResourceOperation,
    bind_resource_activity, record_resource_activity,
)
from src.core.subagent.graph import create_delegate_tool
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

    def test_registry_returns_tools_and_specs_in_name_order(self):
        @tool
        def zebra() -> str:
            """Return the last test tool."""
            return "z"

        @tool
        def alpha() -> str:
            """Return the first test tool."""
            return "a"

        registry = ToolRegistry()
        registry.register(_spec(name="zebra", tool=zebra))
        registry.register(_spec(name="alpha", tool=alpha))
        registry.freeze()

        self.assertEqual(["alpha", "zebra"], [spec.name for spec in registry.specs_for(ToolAudience.PARENT)])
        self.assertEqual(["alpha", "zebra"], [item.name for item in registry.tools_for(ToolAudience.PARENT)])

    def test_failing_pre_hook_rejects_call(self):
        class BrokenHook:
            def handle(self, _context):
                raise RuntimeError("private")

        registry = HookRegistry()
        registry.register(HookSpec(
            "broken", HookPoint.PRE_TOOL_USE, BrokenHook(),
            failure_mode=HookFailureMode.CLOSED,
        ))
        registry.freeze()
        _context, decision = HookDispatcher(registry).dispatch(HookContext(
            HookPoint.PRE_TOOL_USE,
            subject="command",
            payload={"args": self.context.args},
        ))
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

    def test_hard_delete_removes_session_scoped_approval_records_and_keeps_workspace_rules(self):
        decision = DefaultToolPolicyEngine(self.repository).evaluate(
            self.context, rule_key="tool:command", persistable=True
        )
        resolved_request = ApprovalService(self.repository).request(self.context, decision)
        self.repository.apply_response(
            resolved_request["request_id"], ApprovalResponse.DENY_SESSION,
            context=self.context, rule_key="tool:command", persistable=True,
        )
        pending_context = replace(self.context, tool_call_id="pending-delete-call")
        pending_decision = DefaultToolPolicyEngine(self.repository).evaluate(
            pending_context, rule_key="tool:pending", persistable=True
        )
        ApprovalService(self.repository).request(pending_context, pending_decision)
        workspace_context = replace(self.context, tool_call_id="workspace-rule-call")
        workspace_request = ApprovalService(self.repository).request(
            workspace_context,
            decision,
        )
        self.repository.apply_response(
            workspace_request["request_id"], ApprovalResponse.ALLOW_WORKSPACE,
            context=workspace_context,
            rule_key="tool:workspace", persistable=True,
        )

        self.assertTrue(self.workspaces.delete_session(self.session))

        with self.database.connect() as conn:
            self.assertEqual(
                0,
                conn.execute(
                    "SELECT COUNT(*) FROM tool_permission_rules WHERE session_id = ?",
                    (str(self.session.session_id),),
                ).fetchone()[0],
            )
            self.assertEqual(
                1,
                conn.execute(
                    "SELECT COUNT(*) FROM tool_permission_rules WHERE session_id IS NULL",
                ).fetchone()[0],
            )
            self.assertEqual(
                0,
                conn.execute(
                    "SELECT COUNT(*) FROM tool_approval_requests WHERE session_id = ?",
                    (str(self.session.session_id),),
                ).fetchone()[0],
            )
            self.assertEqual(
                0,
                conn.execute(
                    "SELECT COUNT(*) FROM tool_approval_audit WHERE session_id = ?",
                    (str(self.session.session_id),),
                ).fetchone()[0],
            )

    def test_approval_summary_redacts_secret_and_is_single_use(self):
        context = self.context.with_args({
            "command": "curl -H Authorization=secret --token hidden",
            "api_key": "secret",
        })
        decision = DefaultToolPolicyEngine(self.repository).evaluate(
            context, rule_key="tool:command", persistable=True
        )
        request = ApprovalService(self.repository).request(context, decision)
        self.assertEqual("[REDACTED]", request["args"]["api_key"])
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
        with self.database.connect() as conn:
            self.assertEqual(
                1,
                conn.execute(
                    "SELECT COUNT(*) FROM tool_approval_audit WHERE request_id=?",
                    (request["request_id"],),
                ).fetchone()[0],
            )

    def test_invalid_network_policy_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "network_policy"):
            CapabilityEnforcer(network_policy="disabled")

    def test_approval_redacts_before_truncating_long_values(self):
        secret = "s" * 200
        context = self.context.with_args({
            "command": ("x" * 450) + " --token " + secret,
            "nested": {"authorization": secret},
        })
        decision = DefaultToolPolicyEngine(self.repository).evaluate(
            context, rule_key="tool:command", persistable=True
        )
        request = ApprovalService(self.repository).request(context, decision)
        rendered = str(request["args"])
        self.assertNotIn(secret, rendered)
        self.assertIn("[REDACTED]", rendered)

    def test_compound_shell_rule_is_not_persistable(self):
        _key, persistable = command_rule_key("echo ok | grep ok")
        self.assertFalse(persistable)

    def test_quoted_heredoc_parser_failure_is_exact_and_not_persistable(self):
        command = "python3 - <<'EOF'\nprint('ok')\nEOF"

        key, persistable = command_rule_key(command)

        self.assertEqual(f"command-exact:{command}", key)
        self.assertFalse(persistable)

    def test_unexpected_policy_failure_becomes_a_tool_error(self):
        calls = []

        @tool
        def command(command: str) -> str:
            """Record a command that must not run when policy fails."""
            calls.append(command)
            return "ok"

        class BrokenPolicy:
            def evaluate(self, *_args, **_kwargs):
                raise RuntimeError("policy unavailable")

        spec = _spec(
            tool=command,
            approval=ApprovalRequirement.NONE,
        )
        pipeline = ToolExecutionPipeline(
            {"command": spec},
            policy=BrokenPolicy(),
            approvals=ApprovalService(self.repository),
        )
        builder = StateGraph(MessagesState, context_schema=ToolExecutionContext)
        builder.add_node("tools", ObservedToolNode([command], pipeline=pipeline))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(checkpointer=MemorySaver())

        events = list(stream_graph_events(
            graph,
            [AIMessage(content="", tool_calls=[{
                "name": "command",
                "args": {"command": "python -V"},
                "id": "broken-policy",
                "type": "tool_call",
            }])],
            checkpoint_thread_id="broken-policy-thread",
            tool_context=ToolExecutionContext(
                self.context.workspace_id,
                self.context.session_id,
                self.context.execution_id,
                self.context.run_id,
                "parent",
                self.context.workspace_root,
            ),
        ))

        self.assertEqual([], calls)
        self.assertEqual("done", events[-1]["event"])
        result = next(
            item for item in events
            if item["event"] == "step"
            and item["data"].get("type") == "tool_call_result"
        )
        self.assertIn("RuntimeError: policy unavailable", result["data"]["content"])


    def test_fallback_hook_failure_becomes_a_tool_error(self):
        calls = []

        @tool
        def command(command: str) -> str:
            """Record a command that must not run when a hook fails."""
            calls.append(command)
            return "ok"

        class BrokenDispatcher:
            def dispatch(self, _context):
                raise RuntimeError("hook unavailable")

        builder = StateGraph(MessagesState, context_schema=ToolExecutionContext)
        builder.add_node(
            "tools",
            ObservedToolNode([command], hook_dispatcher=BrokenDispatcher()),
        )
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(checkpointer=MemorySaver())

        events = list(stream_graph_events(
            graph,
            [AIMessage(content="", tool_calls=[{
                "name": "command",
                "args": {"command": "python -V"},
                "id": "broken-fallback-hook",
                "type": "tool_call",
            }])],
            checkpoint_thread_id="broken-fallback-hook-thread",
            tool_context=ToolExecutionContext(
                self.context.workspace_id,
                self.context.session_id,
                self.context.execution_id,
                self.context.run_id,
                "parent",
                self.context.workspace_root,
            ),
        ))

        self.assertEqual([], calls)
        self.assertEqual("done", events[-1]["event"])
        result = next(
            item for item in events
            if item["event"] == "step"
            and item["data"].get("type") == "tool_call_result"
        )
        self.assertIn("RuntimeError: hook unavailable", result["data"]["content"])

    def test_post_hook_failure_keeps_the_successful_tool_result(self):
        calls = []

        @tool
        def command(command: str) -> str:
            """Return success even when post-tool observation fails."""
            calls.append(command)
            return "ok"

        class BrokenPostDispatcher:
            def dispatch(self, context):
                if context.point == HookPoint.POST_TOOL_USE:
                    raise RuntimeError("post hook unavailable")
                return context, HookDecision()

        spec = _spec(
            tool=command,
            risk=ToolRisk.READ_ONLY,
            capabilities=frozenset(),
            approval=ApprovalRequirement.NONE,
        )
        pipeline = ToolExecutionPipeline(
            {"command": spec},
            policy=DefaultToolPolicyEngine(self.repository),
            approvals=ApprovalService(self.repository),
            hook_dispatcher=BrokenPostDispatcher(),
        )
        builder = StateGraph(MessagesState, context_schema=ToolExecutionContext)
        builder.add_node("tools", ObservedToolNode([command], pipeline=pipeline))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(checkpointer=MemorySaver())

        events = list(stream_graph_events(
            graph,
            [AIMessage(content="", tool_calls=[{
                "name": "command",
                "args": {"command": "python -V"},
                "id": "broken-post-hook",
                "type": "tool_call",
            }])],
            checkpoint_thread_id="broken-post-hook-thread",
            tool_context=ToolExecutionContext(
                self.context.workspace_id,
                self.context.session_id,
                self.context.execution_id,
                self.context.run_id,
                "parent",
                self.context.workspace_root,
            ),
        ))

        self.assertEqual(["python -V"], calls)
        self.assertEqual("done", events[-1]["event"])
        result = next(
            item for item in events
            if item["event"] == "step"
            and item["data"].get("type") == "tool_call_result"
        )
        self.assertEqual("ok", result["data"]["content"])

    def test_tool_implementation_failure_stays_inside_the_graph(self):
        @tool
        def command(command: str) -> str:
            """Raise one implementation error for fault-containment testing."""
            raise RuntimeError(f"cannot execute {command}")

        spec = _spec(
            tool=command,
            risk=ToolRisk.READ_ONLY,
            capabilities=frozenset(),
            approval=ApprovalRequirement.NONE,
        )
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

        events = list(stream_graph_events(
            graph,
            [AIMessage(content="", tool_calls=[{
                "name": "command",
                "args": {"command": "python -V"},
                "id": "broken-tool",
                "type": "tool_call",
            }])],
            checkpoint_thread_id="broken-tool-thread",
            tool_context=ToolExecutionContext(
                self.context.workspace_id,
                self.context.session_id,
                self.context.execution_id,
                self.context.run_id,
                "parent",
                self.context.workspace_root,
            ),
        ))

        self.assertEqual("done", events[-1]["event"])
        result = next(
            item for item in events
            if item["event"] == "step"
            and item["data"].get("type") == "tool_call_result"
        )
        self.assertIn("RuntimeError: cannot execute python -V", result["data"]["content"])
    def test_workspace_write_rule_matches_descendant_directory(self):
        class WriteTool:
            name = "write_workspace_file"

        spec = _spec(
            name=WriteTool.name,
            tool=WriteTool(),
            capabilities=frozenset({ToolCapability.FILE_WRITE}),
            sandbox=SandboxMode.WORKSPACE_WRITE,
        )
        parent = replace(
            self.context,
            tool_name=WriteTool.name,
            tool_call_id="write-parent",
            args={"path": "src/package/file.py", "content": "x"},
            spec=spec,
        )
        rule_key, persistable = ToolExecutionPipeline._rule_identity(parent)
        decision = PolicyDecision(
            PolicyAction.ASK, "approval", rule_key, persistable, spec.capabilities
        )
        service = ApprovalService(self.repository)
        pending = service.request(parent, decision)
        self.assertTrue(service.resolve_interrupt(
            parent,
            decision,
            pending["request_id"],
            {"request_id": pending["request_id"], "response": "allow_workspace"},
        ))
        child = replace(
            parent,
            tool_call_id="write-child",
            args={"path": "src/package/deeper/other.py", "content": "y"},
        )
        child_key, _ = ToolExecutionPipeline._rule_identity(child)
        self.assertEqual("allow", self.repository.matching_rule(child, child_key))
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

    def test_pipeline_triggers_tool_hooks_once_when_wrapped_by_observed_node(self):
        calls = []

        @tool
        def command(command: str) -> str:
            """Record one direct test command."""
            calls.append(command)
            return "ok"

        class CountingHook:
            def __init__(self):
                self.calls = []

            def handle(self, context):
                self.calls.append(context)
                return HookDecision()

        pre_hook = CountingHook()
        post_hook = CountingHook()
        hooks = HookRegistry()
        hooks.register(HookSpec(
            "pre", HookPoint.PRE_TOOL_USE, pre_hook, matcher="^command$"
        ))
        hooks.register(HookSpec(
            "post", HookPoint.POST_TOOL_USE, post_hook, matcher="^command$"
        ))
        hooks.freeze()
        spec = _spec(
            tool=command,
            risk=ToolRisk.READ_ONLY,
            capabilities=frozenset(),
            approval=ApprovalRequirement.NONE,
        )
        pipeline = ToolExecutionPipeline(
            {"command": spec},
            policy=DefaultToolPolicyEngine(self.repository),
            approvals=ApprovalService(self.repository),
            hook_dispatcher=HookDispatcher(hooks),
        )
        builder = StateGraph(MessagesState, context_schema=ToolExecutionContext)
        builder.add_node("tools", ObservedToolNode([command], pipeline=pipeline))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(checkpointer=MemorySaver())

        events = list(stream_graph_events(
            graph,
            [AIMessage(content="", tool_calls=[{
                "name": "command",
                "args": {"command": "python -V"},
                "id": "hook-once",
                "type": "tool_call",
            }])],
            checkpoint_thread_id="hook-once-thread",
            tool_context=ToolExecutionContext(
                self.context.workspace_id, self.context.session_id,
                self.context.execution_id, self.context.run_id,
                "parent", self.context.workspace_root,
            ),
        ))

        self.assertEqual(["python -V"], calls)
        self.assertEqual("done", events[-1]["event"])
        self.assertEqual(1, len(pre_hook.calls))
        self.assertEqual(1, len(post_hook.calls))
        self.assertEqual("success", post_hook.calls[0].payload["status"])

    def test_delegate_subagent_tools_use_the_configured_hook_dispatcher(self):
        calls = []

        @tool
        def inspect(path: str) -> str:
            """Inspect one test path."""
            calls.append(path)
            record_resource_activity(ResourceObservation(
                f"workspace://{path}", ResourceOperation.READ, ObservationMode.EXACT,
            ))
            return "ok"

        class Model:
            def __init__(self): self.count = 0
            def invoke(self, _messages):
                self.count += 1
                if self.count == 1:
                    return AIMessage(content="", tool_calls=[{
                        "name": "inspect", "args": {"path": "safe.txt"},
                        "id": "subagent-inspect", "type": "tool_call",
                    }])
                return AIMessage(content="done")

        class Provider:
            def create_chat_model(self, *_args, **_kwargs): return Model()

        class CaptureHook:
            def handle(self, context):
                calls.append(f"hook:{context.subject}")
                return HookDecision()

        hooks = HookRegistry()
        hooks.register(HookSpec("subagent-pre", HookPoint.PRE_TOOL_USE, CaptureHook(), matcher="^inspect$"))
        hooks.freeze()
        activity_repo = SQLiteResourceActivityRepository(self.database)
        delegate = create_delegate_tool(
            [inspect], Provider(), hook_dispatcher=HookDispatcher(hooks), max_steps=5,
            resource_activity_recorder=activity_repo,
        )
        with bind_resource_activity(activity_repo, self.context):
            result = delegate.func("inspect", "", {"messages": []})
        activities = activity_repo.list(execution_id=self.context.execution_id)["items"]

        self.assertEqual("done", result)
        self.assertIn("hook:inspect", calls)
        self.assertIn("safe.txt", calls)
        self.assertEqual("subagent", activities[0]["actor"])
        self.assertEqual("workspace://safe.txt", activities[0]["resource_uri"])
    def test_observed_node_fallback_exposes_resource_evidence_to_hook(self):
        @tool
        def inspect(path: str) -> str:
            """Inspect one test path."""
            return path

        class EvidenceRecorder:
            def evidence_for(self, context):
                return {"status": "current", "activity_id": "read-1", "path": context.args["path"]}
            def record(self, _context, _observation):
                return None

        class CaptureHook:
            def __init__(self): self.payload = None
            def handle(self, context):
                self.payload = context.payload
                return HookDecision()

        capture = CaptureHook()
        hooks = HookRegistry()
        hooks.register(HookSpec("capture", HookPoint.PRE_TOOL_USE, capture, matcher="^inspect$"))
        hooks.freeze()
        builder = StateGraph(MessagesState, context_schema=ToolExecutionContext)
        builder.add_node("tools", ObservedToolNode(
            [inspect], hook_dispatcher=HookDispatcher(hooks),
            resource_activity_recorder=EvidenceRecorder(),
        ))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(checkpointer=MemorySaver())
        list(stream_graph_events(
            graph,
            [AIMessage(content="", tool_calls=[{
                "name": "inspect", "args": {"path": "./a.py"},
                "id": "fallback-evidence", "type": "tool_call",
            }])],
            checkpoint_thread_id="fallback-evidence-thread",
            tool_context=ToolExecutionContext(
                self.context.workspace_id, self.context.session_id,
                self.context.execution_id, self.context.run_id,
                "subagent", self.context.workspace_root,
            ),
        ))
        self.assertEqual("current", capture.payload["resource_evidence"]["status"])
        self.assertEqual("./a.py", capture.payload["resource_evidence"]["path"])
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

    def test_permission_hook_can_allow_once_without_persisting_rule(self):
        calls = []

        @tool
        def command(command: str) -> str:
            """Record one automatically approved test command."""
            calls.append(command)
            return "ok"

        class ApprovalAgent:
            def handle(self, _context):
                return HookDecision(HookAction.ALLOW_ONCE, reason="low risk")

        hooks = HookRegistry()
        hooks.register(HookSpec(
            "approval-agent", HookPoint.PERMISSION_REQUEST, ApprovalAgent(),
            matcher="^command$",
        ))
        hooks.freeze()
        spec = _spec(tool=command)
        pipeline = ToolExecutionPipeline(
            {"command": spec},
            policy=DefaultToolPolicyEngine(self.repository),
            approvals=ApprovalService(self.repository),
            hook_dispatcher=HookDispatcher(hooks),
        )
        builder = StateGraph(MessagesState, context_schema=ToolExecutionContext)
        builder.add_node("tools", ObservedToolNode([command], pipeline=pipeline))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(checkpointer=MemorySaver())
        events = list(stream_graph_events(
            graph,
            [AIMessage(content="", tool_calls=[{
                "name": "command", "args": {"command": "python -V"},
                "id": "auto-approval", "type": "tool_call",
            }])],
            checkpoint_thread_id="auto-approval-thread",
            tool_context=ToolExecutionContext(
                self.context.workspace_id, self.context.session_id,
                self.context.execution_id, self.context.run_id,
                "parent", self.context.workspace_root,
            ),
        ))
        self.assertEqual(["python -V"], calls)
        self.assertNotIn("tool_approval_required", {item["event"] for item in events})
        with self.database.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM tool_permission_rules").fetchone()[0]
            requests = conn.execute("SELECT COUNT(*) FROM tool_approval_requests WHERE status='resolved'").fetchone()[0]
            audits = conn.execute("SELECT COUNT(*) FROM tool_approval_audit").fetchone()[0]
        self.assertEqual(0, count)
        self.assertEqual(1, requests)
        self.assertEqual(1, audits)

    def test_approval_strategy_registry_accepts_extension_and_rejects_duplicates(self):
        class DenyAll:
            name = "deny_all"

            def decide(self, _context, _decision):
                return ApprovalStrategyDecision(
                    ApprovalStrategyAction.AUTO_DENY,
                    ApprovalResponse.DENY_ONCE,
                )

        registry = ApprovalStrategyRegistry()
        registry.register(DenyAll())

        self.assertEqual("deny_all", registry.get("deny_all").name)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            registry.register(DenyAll())

    def test_accept_all_resolves_ask_once_without_persistent_rule(self):
        calls = []

        @tool
        def command(command: str) -> str:
            """Record an automatically accepted command."""
            calls.append(command)
            return "ok"

        registry = ApprovalStrategyRegistry()
        resolver = ApprovalModeResolver(
            self.repository,
            registry,
            default_mode="accept_all",
        )
        approvals = ApprovalService(self.repository)
        coordinator = ApprovalCoordinator(approvals, resolver, registry)
        spec = _spec(tool=command)
        pipeline = ToolExecutionPipeline(
            {"command": spec},
            policy=DefaultToolPolicyEngine(self.repository),
            approvals=approvals,
            approval_coordinator=coordinator,
        )
        builder = StateGraph(MessagesState, context_schema=ToolExecutionContext)
        builder.add_node("tools", ObservedToolNode([command], pipeline=pipeline))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(checkpointer=MemorySaver())

        events = list(stream_graph_events(
            graph,
            [AIMessage(content="", tool_calls=[{
                "name": "command", "args": {"command": "python -V"},
                "id": "accept-all-call", "type": "tool_call",
            }])],
            checkpoint_thread_id="accept-all-thread",
            tool_context=ToolExecutionContext(
                self.context.workspace_id, self.context.session_id,
                self.context.execution_id, self.context.run_id,
                "parent", self.context.workspace_root,
            ),
        ))

        self.assertEqual(["python -V"], calls)
        self.assertNotIn("tool_approval_required", {item["event"] for item in events})
        with self.database.connect() as conn:
            request = conn.execute(
                "SELECT status, response, approval_mode FROM tool_approval_requests "
                "WHERE tool_call_id='accept-all-call'"
            ).fetchone()
            audit = conn.execute(
                "SELECT decision_source, approval_mode FROM tool_approval_audit "
                "WHERE tool_call_id='accept-all-call'"
            ).fetchone()
            rules = conn.execute(
                "SELECT COUNT(*) FROM tool_permission_rules"
            ).fetchone()[0]
        self.assertEqual(("resolved", "allow_once", "accept_all"), tuple(request))
        self.assertEqual(("automatic", "accept_all"), tuple(audit))
        self.assertEqual(0, rules)

    def test_accept_all_cannot_bypass_capability_enforcer(self):
        calls = []

        @tool
        def command(command: str) -> str:
            """Record a command that a hard boundary must prevent."""
            calls.append(command)
            return "ok"

        class RejectingEnforcer:
            def validate(self, _context):
                raise PermissionError("hard boundary")

        registry = ApprovalStrategyRegistry()
        resolver = ApprovalModeResolver(
            self.repository,
            registry,
            default_mode="accept_all",
        )
        approvals = ApprovalService(self.repository)
        pipeline = ToolExecutionPipeline(
            {"command": _spec(tool=command)},
            policy=DefaultToolPolicyEngine(self.repository),
            approvals=approvals,
            approval_coordinator=ApprovalCoordinator(
                approvals, resolver, registry
            ),
            enforcer=RejectingEnforcer(),
        )
        builder = StateGraph(MessagesState, context_schema=ToolExecutionContext)
        builder.add_node("tools", ObservedToolNode([command], pipeline=pipeline))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(checkpointer=MemorySaver())

        events = list(stream_graph_events(
            graph,
            [AIMessage(content="", tool_calls=[{
                "name": "command", "args": {"command": "python -V"},
                "id": "accept-all-hard-deny", "type": "tool_call",
            }])],
            checkpoint_thread_id="accept-all-hard-deny-thread",
            tool_context=ToolExecutionContext(
                self.context.workspace_id, self.context.session_id,
                self.context.execution_id, self.context.run_id,
                "parent", self.context.workspace_root,
            ),
        ))

        self.assertEqual([], calls)
        self.assertNotIn("tool_approval_required", {item["event"] for item in events})
        with self.database.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM tool_approval_requests "
                "WHERE tool_call_id='accept-all-hard-deny'"
            ).fetchone()[0]
        self.assertEqual(0, count)

    def test_mode_switch_does_not_resolve_existing_manual_request(self):
        registry = ApprovalStrategyRegistry()
        resolver = ApprovalModeResolver(
            self.repository,
            registry,
            default_mode="manual",
        )
        coordinator = ApprovalCoordinator(
            ApprovalService(self.repository), resolver, registry
        )
        decision = DefaultToolPolicyEngine(self.repository).evaluate(
            self.context, rule_key="tool:command", persistable=True
        )

        first = coordinator.begin(self.context, decision)
        self.repository.set_session_mode(
            self.context.workspace_id,
            self.context.session_id,
            "accept_all",
        )
        second = coordinator.begin(self.context, decision)

        self.assertIsNone(first.allowed)
        self.assertIsNone(second.allowed)
        self.assertEqual(first.request["request_id"], second.request["request_id"])
        self.assertEqual("manual", second.request["approval_mode"])

    def test_unknown_persisted_mode_falls_back_to_manual(self):
        registry = ApprovalStrategyRegistry()
        self.repository.set_session_mode(
            self.context.workspace_id,
            self.context.session_id,
            "future_mode",
        )
        resolver = ApprovalModeResolver(
            self.repository,
            registry,
            default_mode="accept_all",
        )

        self.assertEqual("manual", resolver.resolve(self.context))


if __name__ == "__main__":
    unittest.main()
