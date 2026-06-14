"""Non-recursive sub-agent prompts."""

SUBAGENT_PROMPT_VERSION = "v1"
SUBAGENT_SYSTEM_PROMPT = (
    "You are a focused non-recursive coding sub-agent. Use the available "
    "workspace-bound tools and return compact findings with evidence. "
    "You cannot delegate to another agent."
)


def build_subagent_task_prompt(task: str, context: str, parent_context: str) -> str:
    """Build one explicit delegated task without hiding parent context policy."""
    return (
        f"Task:\n{task}\n\nExtra context:\n{context or '(none)'}\n\n"
        f"Recent parent context:\n{parent_context or '(none)'}"
    )
