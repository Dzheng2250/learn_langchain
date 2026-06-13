"""Workspace-bound skill tools."""

from pathlib import Path

from langchain_core.tools import tool

from src.core.skills.store import LocalSkillStore


def create_skill_tools(root: Path) -> tuple[object, object, LocalSkillStore]:
    """Create skill manifest and document tools bound to ``root``."""
    store = LocalSkillStore(str(root))

    @tool
    def list_skills() -> str:
        """List skill manifests available in the current workspace."""
        return store.format_skill_list()

    @tool
    def read_skill(skill_name: str) -> str:
        """Read one current-workspace SKILL.md by skill directory or name."""
        try:
            return store.read_skill(skill_name)
        except ValueError as exc:
            return f"Skill read rejected: {exc}"

    return list_skills, read_skill, store
