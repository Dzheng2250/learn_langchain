"""Workspace-bound toolset composition."""

from dataclasses import dataclass

from src.core.subagent.graph import create_delegate_tool
from src.core.tools.commands import create_run_command_in_container
from src.core.tools.skills import create_skill_tools
from src.core.tools.summarization import create_summarize_large_file
from src.core.tools.weather import get_weather
from src.core.tools.workspace import create_workspace_file_tools
from src.core.workspace.models import WorkspaceContext


@dataclass(frozen=True)
class WorkspaceToolset:
    base_tools: list
    parent_tools: list
    skill_manifest: str


def create_workspace_toolset(workspace: WorkspaceContext) -> WorkspaceToolset:
    read_file, read_entire, read_lite = create_workspace_file_tools(workspace.root)
    list_skills, read_skill, skill_store = create_skill_tools(workspace.root)
    summarize = create_summarize_large_file(workspace.root)
    command = create_run_command_in_container(workspace.root)
    base_tools = [get_weather, read_file, read_entire, list_skills, read_skill, summarize, command]
    delegate = create_delegate_tool(base_tools)
    parent_tools = [get_weather, read_lite, list_skills, read_skill, command, delegate]
    return WorkspaceToolset(base_tools, parent_tools, skill_store.format_skill_list())
