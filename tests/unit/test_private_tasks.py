import unittest
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from src.config.tasks import TaskSettings
from src.core.adapters.sqlite.tool_approvals import SQLiteToolApprovalRepository
from src.core.llm.provider import LlmPurpose
from src.core.state import ExecutionRepository, LocalStateDatabase, LocalWorkspaceRepository
from src.core.tasks import (
    TaskPlanningService,
    TaskRepository,
    TaskStatus,
    ToolExecutionContext,
    create_task_tools,
)
from src.core.tasks.models import TaskPlanItem
from src.core.tasks.validation import TaskPlanValidator
from src.core.tools.catalog import (
    ApprovalRequirement,
    ToolAudience,
    ToolCapability,
    ToolRisk,
    ToolSpec,
)
from src.core.tools.observed import ObservedToolNode
from src.core.tools.registry import create_workspace_toolset
from src.core.tools.security.approval import ApprovalService
from src.core.tools.security.pipeline import ToolExecutionPipeline
from src.core.tools.security.policy import DefaultToolPolicyEngine


class FakeChatModel:
    def invoke(self, _messages):
        return AIMessage(content="")

    def bind_tools(self, _tools):
        return self


class FakeProvider:
    def create_chat_model(self, purpose, **_kwargs):
        self.last_purpose = purpose
        return FakeChatModel()


class PrivateTaskTest(unittest.TestCase):
    def setUp(self):
        self.database = LocalStateDatabase(":memory:")
        self.addCleanup(self.database.close)
        self.database.initialize()
        workspace_repo = LocalWorkspaceRepository(self.database)
        self.workspace = workspace_repo.resolve(str(Path("tests/fixtures/workspace_a").resolve()))
        self.session, _ = workspace_repo.resolve_session(self.workspace, "default")
        self.executions = ExecutionRepository(self.database)
        self.execution = self.executions.begin(self.session, "large goal")
        self.context = ToolExecutionContext(
            str(self.workspace.workspace_id),
            str(self.session.session_id),
            self.execution.execution_id,
        )
        settings = TaskSettings(max_tasks_per_execution=4)
        self.repository = TaskRepository(self.database, settings)
        self.service = TaskPlanningService(self.repository, settings)

    def test_task_validator_rejects_invalid_keys_and_cycles(self):
        validator = TaskPlanValidator(TaskSettings(max_tasks_per_execution=4))

        with self.assertRaisesRegex(ValueError, "task_key"):
            validator.validate_plan_item(TaskPlanItem("BadKey", "Bad"))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validator.validate_unique_keys(
                [
                    TaskPlanItem("inspect", "Inspect"),
                    TaskPlanItem("inspect", "Duplicate"),
                ]
            )
        with self.assertRaisesRegex(ValueError, "cycle"):
            validator.assert_acyclic({"first": {"second"}, "second": {"first"}})

    def test_schema_version_four_creates_task_tables(self):
        with self.database.connect() as conn:
            version = conn.execute(
                "SELECT MAX(version) FROM local_schema_migrations"
            ).fetchone()[0]
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }

        self.assertGreaterEqual(version, 4)
        self.assertIn("execution_tasks", tables)
        self.assertIn("execution_task_dependencies", tables)

    def test_plan_is_atomic_when_dependency_is_invalid(self):
        with self.assertRaisesRegex(ValueError, "unknown dependency"):
            self.service.plan(
                self.context,
                [
                    {"task_key": "inspect", "subject": "Inspect"},
                    {
                        "task_key": "write_report",
                        "subject": "Write report",
                        "depends_on": ["missing"],
                    },
                ],
            )

        self.assertEqual([], self.repository.list(self.context))

    def test_dependency_cycle_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.service.plan(
                self.context,
                [
                    {
                        "task_key": "first",
                        "subject": "First",
                        "depends_on": ["second"],
                    },
                    {
                        "task_key": "second",
                        "subject": "Second",
                        "depends_on": ["first"],
                    },
                ],
            )

    def test_downstream_task_becomes_ready_when_dependency_completes(self):
        self.service.plan(
            self.context,
            [
                {"task_key": "inspect", "subject": "Inspect"},
                {
                    "task_key": "write_report",
                    "subject": "Write report",
                    "depends_on": ["inspect"],
                },
            ],
        )

        blocked = self.repository.get(self.context, "write_report")
        self.assertEqual(("inspect",), blocked.blocked_by)
        with self.assertRaisesRegex(ValueError, "blocked"):
            self.service.update(self.context, "write_report", status=TaskStatus.IN_PROGRESS)

        self.service.update(self.context, "inspect", status=TaskStatus.COMPLETED)

        ready = self.repository.get(self.context, "write_report")
        self.assertEqual((), ready.blocked_by)
        self.assertTrue(ready.ready)

    def test_task_list_uses_user_facing_state_phrases(self):
        result = self.service.plan(
            self.context,
            [
                {"task_key": "inspect", "subject": "Inspect"},
                {
                    "task_key": "write_report",
                    "subject": "Write report",
                    "depends_on": ["inspect"],
                },
            ],
        )

        self.assertIn("[ ] inspect: Inspect (ready)", result)
        self.assertIn("[ ] write_report: Write report (waiting for: inspect)", result)
        self.assertNotIn("blocked_by", result)
        self.assertNotIn("depends_on", result)

    def test_task_update_returns_refreshed_plan_list(self):
        self.service.plan(
            self.context,
            [
                {"task_key": "inspect", "subject": "Inspect"},
                {
                    "task_key": "write_report",
                    "subject": "Write report",
                    "depends_on": ["inspect"],
                },
            ],
        )

        result = self.service.update(
            self.context,
            "inspect",
            status=TaskStatus.COMPLETED,
        )

        self.assertIn("Task updated: inspect", result)
        self.assertIn("[x] inspect: Inspect (completed)", result)
        self.assertIn("[ ] write_report: Write report (ready)", result)

    def test_task_update_rejects_empty_change(self):
        self.service.plan(
            self.context,
            [{"task_key": "inspect", "subject": "Inspect"}],
        )

        with self.assertRaisesRegex(ValueError, "requires a change"):
            self.service.update(self.context, "inspect")

    def test_task_get_uses_user_facing_dependency_labels(self):
        self.service.plan(
            self.context,
            [
                {"task_key": "inspect", "subject": "Inspect"},
                {
                    "task_key": "write_report",
                    "subject": "Write report",
                    "depends_on": ["inspect"],
                },
            ],
        )

        result = self.service.get(self.context, "write_report")

        self.assertIn("state: waiting for: inspect", result)
        self.assertIn("depends on: inspect", result)
        self.assertIn("waiting for: inspect", result)
        self.assertNotIn("blocked_by", result)

    def test_execution_context_prevents_cross_execution_reads(self):
        self.service.plan(self.context, [{"task_key": "inspect", "subject": "Inspect"}])
        other = self.executions.begin(
            LocalWorkspaceRepository(self.database).resolve_session(self.workspace, "other")[0],
            "other goal",
        )
        other_context = ToolExecutionContext(
            str(self.workspace.workspace_id),
            str(self.session.session_id),
            other.execution_id,
        )

        with self.assertRaisesRegex(ValueError, "does not match"):
            self.repository.list(other_context)

    def test_task_tool_runtime_is_not_exposed_to_llm_schema(self):
        tools = create_task_tools(self.service)

        schemas = {tool.name: convert_to_openai_tool(tool)["function"]["parameters"] for tool in tools}

        self.assertNotIn("runtime", str(schemas))
        self.assertEqual({"properties": {}, "type": "object"}, schemas["task_list"])
        self.assertIn("status", schemas["task_update"]["required"])
        self.assertEqual(
            ["pending", "in_progress", "completed", "cancelled"],
            schemas["task_update"]["properties"]["status"]["enum"],
        )

    def test_task_plan_executes_through_security_pipeline_with_injected_runtime(self):
        task_plan = next(
            tool for tool in create_task_tools(self.service) if tool.name == "task_plan"
        )
        spec = ToolSpec(
            name="task_plan",
            tool=task_plan,
            audiences=frozenset({ToolAudience.PARENT}),
            risk=ToolRisk.INTERNAL_STATE,
            capabilities=frozenset({ToolCapability.INTERNAL_STATE}),
            approval=ApprovalRequirement.NONE,
        )
        approvals = SQLiteToolApprovalRepository(self.database)
        pipeline = ToolExecutionPipeline(
            {"task_plan": spec},
            policy=DefaultToolPolicyEngine(approvals),
            approvals=ApprovalService(approvals),
        )
        builder = StateGraph(MessagesState, context_schema=ToolExecutionContext)
        builder.add_node("tools", ObservedToolNode([task_plan], pipeline=pipeline))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(checkpointer=MemorySaver())
        tool_call = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "task_plan",
                    "args": {
                        "tasks": [
                            {
                                "task_key": "implement",
                                "subject": "Implement factorial",
                            }
                        ]
                    },
                    "id": "task-plan-call",
                    "type": "tool_call",
                }
            ],
        )

        result = graph.invoke(
            {"messages": [tool_call]},
            config={"configurable": {"thread_id": "task-plan-pipeline"}},
            context=self.context,
        )

        self.assertIn("Task plan saved.", result["messages"][-1].content)
        self.assertIsNotNone(self.repository.get(self.context, "implement"))

    def test_invalid_task_update_becomes_model_visible_tool_error(self):
        self.service.plan(
            self.context,
            [{"task_key": "implement", "subject": "Implement factorial"}],
        )
        task_update = next(
            tool for tool in create_task_tools(self.service)
            if tool.name == "task_update"
        )
        spec = ToolSpec(
            name="task_update",
            tool=task_update,
            audiences=frozenset({ToolAudience.PARENT}),
            risk=ToolRisk.INTERNAL_STATE,
            capabilities=frozenset({ToolCapability.INTERNAL_STATE}),
            approval=ApprovalRequirement.NONE,
        )
        approvals = SQLiteToolApprovalRepository(self.database)
        pipeline = ToolExecutionPipeline(
            {"task_update": spec},
            policy=DefaultToolPolicyEngine(approvals),
            approvals=ApprovalService(approvals),
        )
        builder = StateGraph(MessagesState, context_schema=ToolExecutionContext)
        builder.add_node("tools", ObservedToolNode([task_update], pipeline=pipeline))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile(checkpointer=MemorySaver())
        tool_call = AIMessage(
            content="",
            tool_calls=[{
                "name": "task_update",
                "args": {"task_key": "implement", "status": None},
                "id": "invalid-task-update",
                "type": "tool_call",
            }],
        )

        result = graph.invoke(
            {"messages": [tool_call]},
            config={"configurable": {"thread_id": "invalid-task-update"}},
            context=self.context,
        )

        tool_result = result["messages"][-1]
        self.assertEqual("error", tool_result.status)
        self.assertIn("ValidationError", tool_result.content)
        self.assertEqual("pending", self.repository.get(self.context, "implement").status)

    def test_task_tools_return_clear_error_without_execution_context(self):
        task_list = next(tool for tool in create_task_tools(self.service) if tool.name == "task_list")

        result = task_list.func(SimpleNamespace(context=None))

        self.assertIn("Task tool error", result)

    def test_has_unfinished_tracks_actionable_task_statuses(self):
        self.assertFalse(self.service.has_unfinished(self.context))
        self.service.plan(
            self.context,
            [{
                "task_key": "verify",
                "subject": "Verify behavior",
                "description": "Run focused tests",
            }],
        )

        self.assertTrue(self.service.has_unfinished(self.context))

        self.service.update(
            self.context,
            "verify",
            status="completed",
        )
        self.assertFalse(self.service.has_unfinished(self.context))

    def test_task_tools_are_parent_only_internal_state_tools(self):
        normal_toolset = create_workspace_toolset(
            self.workspace,
            FakeProvider(),
        )
        toolset = create_workspace_toolset(
            self.workspace,
            FakeProvider(),
            task_service=self.service,
        )

        self.assertFalse(
            {"task_plan", "task_update", "task_list", "task_get"}
            & {tool.name for tool in normal_toolset.parent_tools}
        )
        parent_names = {tool.name for tool in toolset.parent_tools}
        subagent_names = {tool.name for tool in toolset.base_tools}
        task_specs = [
            spec for spec in toolset.registry.specs() if spec.name.startswith("task_")
        ]

        self.assertTrue({"task_plan", "task_update", "task_list", "task_get"} <= parent_names)
        self.assertFalse({"task_plan", "task_update", "task_list", "task_get"} & subagent_names)
        self.assertTrue(task_specs)
        self.assertTrue(all(spec.risk == ToolRisk.INTERNAL_STATE for spec in task_specs))
        self.assertTrue(all(spec.audiences == frozenset({ToolAudience.PARENT}) for spec in task_specs))


if __name__ == "__main__":
    unittest.main()
