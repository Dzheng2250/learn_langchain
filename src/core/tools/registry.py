"""Workspace-bound toolset composition."""

from dataclasses import dataclass

from src.config.settings import SUBAGENT_MAX_STEPS
from src.core.llm.contracts import ModelProvider
from src.core.subagent.graph import create_delegate_tool
from src.core.tasks.service import TaskPlanningService
from src.core.tasks.tools import create_task_tools
from src.core.tools.catalog import ToolAudience, ToolRegistry, ToolRisk, ToolSpec
from src.core.tools.commands import create_run_command_in_container
from src.core.tools.skills import create_skill_tools
from src.core.tools.summarization import create_summarize_large_file
from src.core.tools.weather import get_weather
from src.core.tools.workspace import create_workspace_file_tools
from src.core.workspace.models import WorkspaceContext


@dataclass(frozen=True)
class WorkspaceToolset:
    """Audience-specific tool views and manifest bound to one Workspace."""
    registry: ToolRegistry
    base_tools: list
    parent_tools: list
    skill_manifest: str


def create_workspace_toolset(
    workspace: WorkspaceContext,
    model_provider: ModelProvider,
    *,
    subagent_max_steps: int = SUBAGENT_MAX_STEPS,
    task_service: TaskPlanningService | None = None,
) -> WorkspaceToolset:
    """Create, classify, and freeze all tools available in one Workspace."""
    provider = model_provider
    read_file, read_entire, read_lite = create_workspace_file_tools(workspace.root)
    list_skills, read_skill, skill_store = create_skill_tools(workspace.root)
    summarize = create_summarize_large_file(workspace.root, provider)
    command = create_run_command_in_container(workspace.root)
    registry = ToolRegistry()

    def register(tool, audiences, risk, description=""):
        """Register one LangChain tool with audience and risk metadata."""
        registry.register(
            ToolSpec(
                name=tool.name,
                tool=tool,
                audiences=frozenset(audiences),
                risk=risk,
                description=description or getattr(tool, "description", ""),
            )
        )

    both = {ToolAudience.PARENT, ToolAudience.SUBAGENT}
    register(get_weather, both, ToolRisk.READ_ONLY)
    register(read_file, {ToolAudience.SUBAGENT}, ToolRisk.READ_ONLY)
    register(read_entire, {ToolAudience.SUBAGENT}, ToolRisk.READ_ONLY)
    register(read_lite, {ToolAudience.PARENT}, ToolRisk.READ_ONLY)
    register(list_skills, both, ToolRisk.READ_ONLY)
    register(read_skill, both, ToolRisk.READ_ONLY)
    register(summarize, {ToolAudience.SUBAGENT}, ToolRisk.READ_ONLY)
    register(command, both, ToolRisk.CONTROLLED_EXECUTION)
    if task_service is not None:
        for task_tool in create_task_tools(task_service):
            register(task_tool, {ToolAudience.PARENT}, ToolRisk.INTERNAL_STATE)

    base_tools = registry.tools_for(ToolAudience.SUBAGENT)
    base_risks = {spec.name: spec.risk for spec in registry.specs_for(ToolAudience.SUBAGENT)}
    delegate = create_delegate_tool(
        base_tools,
        provider,
        max_steps=subagent_max_steps,
        risk_by_name=base_risks,
    )
    register(delegate, {ToolAudience.PARENT}, ToolRisk.DELEGATION)
    registry.freeze()
    return WorkspaceToolset(
        registry=registry,
        base_tools=base_tools,
        parent_tools=registry.tools_for(ToolAudience.PARENT),
        skill_manifest=skill_store.format_skill_list(),
    )
