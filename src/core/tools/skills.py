
from langchain_core.tools import tool

from src.core.common.debug import debug_print
from src.core.skills.store import LocalSkillStore
from src.core.tools.workspace import WORKSPACE_DIR


skill_store = LocalSkillStore(WORKSPACE_DIR)


@tool
def list_skills() -> str:
    """List available local skills under the configured skills directory."""
    debug_print("TOOL list_skills INPUT", f"skills_dir={skill_store.skills_dir!r}")
    result = skill_store.format_skill_list()
    debug_print("TOOL list_skills OUTPUT", result)
    return result


@tool
def read_skill(skill_name: str) -> str:
    """Read a specific local skill's SKILL.md file by skill directory name."""
    debug_print("TOOL read_skill INPUT", f"skill_name={skill_name!r}")

    try:
        result = skill_store.read_skill(skill_name)
    except ValueError as exc:
        result = f"Skill read rejected: {exc}"
        debug_print("TOOL read_skill OUTPUT", result)
        return result

    debug_print("TOOL read_skill OUTPUT", result)
    return result
