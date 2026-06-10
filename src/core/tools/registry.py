from src.core.tools.commands import run_command_in_container
from src.core.tools.skills import list_skills, read_skill, skill_store
from src.core.tools.summarization import summarize_large_file
from src.core.tools.weather import get_weather
from src.core.tools.workspace import read_entire_file, read_workspace_file, read_workspace_file_lite


# Sub-agent tools intentionally exclude delegation to prevent recursive agents.
base_tools = [
    get_weather,
    read_workspace_file,
    read_entire_file,
    list_skills,
    read_skill,
    summarize_large_file,
    run_command_in_container,
]

# Parent tools keep file reads lightweight; broad tasks should be delegated.
parent_base_tools = [
    get_weather,
    read_workspace_file_lite,
    list_skills,
    read_skill,
    run_command_in_container,
]

__all__ = ["base_tools", "parent_base_tools", "skill_store"]
