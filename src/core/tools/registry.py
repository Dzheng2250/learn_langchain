"""Workspace-bound toolset composition."""

from dataclasses import dataclass

from src.config.settings import SUBAGENT_MAX_STEPS
from src.core.llm.contracts import ModelProvider
from src.core.subagent.graph import create_delegate_tool
from src.core.tasks.service import TaskPlanningService
from src.core.tasks.tools import create_task_tools
from src.core.tools.catalog import (
    ApprovalRequirement, SandboxMode, ToolAudience, ToolCapability,
    ToolRegistry, ToolRisk, ToolSpec,
)
from src.core.tools.security import (
    ApprovalCoordinator,
    ApprovalService,
    DefaultToolPolicyEngine,
    ToolExecutionPipeline,
)
from src.core.tools.security.enforcement import CapabilityEnforcer
from src.core.tools.command_changes import create_staged_command_tools
from src.core.tools.commands import create_run_command_in_container
from src.core.tools.skills import create_skill_tools
from src.core.tools.summarization import create_summarize_large_file
from src.core.tools.weather import get_weather
from src.core.tools.workspace import create_workspace_file_tools
from src.core.tools.workspace_write import create_workspace_write_tools
from src.core.workspace.models import WorkspaceContext


@dataclass(frozen=True)
class WorkspaceToolset:
    """Audience-specific tool views and manifest bound to one Workspace."""
    registry: ToolRegistry
    base_tools: list
    parent_tools: list
    skill_manifest: str
    pipeline: object | None = None


def create_workspace_toolset(
    workspace: WorkspaceContext,
    model_provider: ModelProvider,
    *,
    subagent_max_steps: int = SUBAGENT_MAX_STEPS,
    task_service: TaskPlanningService | None = None,
    approval_repository=None,
    host_execution_enabled: bool = False,
    approval_mode_resolver=None,
    approval_strategy_registry=None,
    default_timeout_seconds: float = 60.0,
    network_policy: str = "deny",
    file_write_enabled: bool = True,
    command_write_enabled: bool = False,
    file_write_max_bytes: int = 1_048_576,
    file_operation_max_entries: int = 100,
    command_changeset_max_files: int = 100,
    command_changeset_max_bytes: int = 10_485_760,
    hook_dispatcher=None,
    resource_activity_recorder=None,
) -> WorkspaceToolset:
    """Create, classify, and freeze all tools available in one Workspace."""
    provider = model_provider
    read_file, read_entire, read_lite = create_workspace_file_tools(workspace.root)
    list_skills, read_skill, skill_store = create_skill_tools(workspace.root)
    summarize = create_summarize_large_file(workspace.root, provider)
    command = create_run_command_in_container(workspace.root)
    write_tools = (
        create_workspace_write_tools(
            workspace.root,
            max_bytes=file_write_max_bytes,
            max_entries=file_operation_max_entries,
        )
        if file_write_enabled else ()
    )
    staged_command_tools = (
        create_staged_command_tools(
            workspace.root,
            max_files=command_changeset_max_files,
            max_bytes=command_changeset_max_bytes,
        )
        if command_write_enabled else ()
    )
    registry = ToolRegistry()

    def register(
        tool, audiences, risk, description="", *, capabilities=(),
        approval=ApprovalRequirement.NONE,
        sandbox=SandboxMode.ISOLATED_READ_ONLY, timeout_seconds=None,
    ):
        """Register one LangChain tool with audience and risk metadata."""
        registry.register(
            ToolSpec(
                name=tool.name,
                tool=tool,
                audiences=frozenset(audiences),
                risk=risk,
                description=description or getattr(tool, "description", ""),
                capabilities=frozenset(capabilities),
                approval=approval,
                sandbox=sandbox,
                timeout_seconds=timeout_seconds or default_timeout_seconds,
            )
        )

    both = {ToolAudience.PARENT, ToolAudience.SUBAGENT}
    register(get_weather, both, ToolRisk.READ_ONLY)
    register(read_file, {ToolAudience.SUBAGENT}, ToolRisk.READ_ONLY, capabilities={ToolCapability.FILE_READ})
    register(read_entire, {ToolAudience.SUBAGENT}, ToolRisk.READ_ONLY, capabilities={ToolCapability.FILE_READ})
    register(read_lite, {ToolAudience.PARENT}, ToolRisk.READ_ONLY, capabilities={ToolCapability.FILE_READ})
    register(list_skills, both, ToolRisk.READ_ONLY)
    register(read_skill, both, ToolRisk.READ_ONLY)
    register(summarize, {ToolAudience.SUBAGENT}, ToolRisk.READ_ONLY)
    register(
        command,
        {ToolAudience.PARENT},
        ToolRisk.CONTROLLED_EXECUTION,
        capabilities={ToolCapability.COMMAND_EXECUTION, ToolCapability.FILE_READ},
        approval=ApprovalRequirement.POLICY,
    )
    for write_tool in write_tools:
        register(
            write_tool,
            {ToolAudience.PARENT},
            ToolRisk.CONTROLLED_EXECUTION,
            capabilities={ToolCapability.FILE_WRITE},
            approval=ApprovalRequirement.POLICY,
            sandbox=SandboxMode.WORKSPACE_WRITE,
        )
    if staged_command_tools:
        stage, apply_changes, discard_changes = staged_command_tools
        register(
            stage,
            {ToolAudience.PARENT},
            ToolRisk.CONTROLLED_EXECUTION,
            capabilities={ToolCapability.COMMAND_EXECUTION, ToolCapability.FILE_READ},
            approval=ApprovalRequirement.POLICY,
        )
        register(
            apply_changes,
            {ToolAudience.PARENT},
            ToolRisk.CONTROLLED_EXECUTION,
            capabilities={ToolCapability.FILE_WRITE},
            approval=ApprovalRequirement.ALWAYS,
            sandbox=SandboxMode.WORKSPACE_WRITE,
        )
        register(
            discard_changes,
            {ToolAudience.PARENT},
            ToolRisk.INTERNAL_STATE,
            capabilities={ToolCapability.INTERNAL_STATE},
        )
    if task_service is not None:
        for task_tool in create_task_tools(task_service):
            register(
                task_tool, {ToolAudience.PARENT}, ToolRisk.INTERNAL_STATE,
                capabilities={ToolCapability.INTERNAL_STATE},
            )

    base_tools = registry.tools_for(ToolAudience.SUBAGENT)
    base_risks = {spec.name: spec.risk for spec in registry.specs_for(ToolAudience.SUBAGENT)}
    delegate = create_delegate_tool(
        base_tools,
        provider,
        max_steps=subagent_max_steps,
        risk_by_name=base_risks,
        hook_dispatcher=hook_dispatcher,
        workspace=workspace,
        resource_activity_recorder=resource_activity_recorder,
    )
    register(
        delegate, {ToolAudience.PARENT}, ToolRisk.DELEGATION,
        capabilities={ToolCapability.DELEGATION},
    )
    registry.freeze()
    pipeline = None
    if approval_repository is not None:
        approvals = ApprovalService(approval_repository)
        coordinator = (
            ApprovalCoordinator(
                approvals,
                approval_mode_resolver,
                approval_strategy_registry,
            )
            if approval_mode_resolver is not None
            and approval_strategy_registry is not None
            else None
        )
        pipeline = ToolExecutionPipeline(
            {spec.name: spec for spec in registry.specs()},
            policy=DefaultToolPolicyEngine(
                approval_repository,
                host_execution_enabled=host_execution_enabled,
            ),
            approvals=approvals,
            approval_coordinator=coordinator,
            hook_dispatcher=hook_dispatcher,
            enforcer=CapabilityEnforcer(network_policy=network_policy),
            resource_activity_recorder=resource_activity_recorder,
        )
    return WorkspaceToolset(
        registry=registry,
        base_tools=base_tools,
        parent_tools=registry.tools_for(ToolAudience.PARENT),
        skill_manifest=skill_store.format_skill_list(),
        pipeline=pipeline,
    )
