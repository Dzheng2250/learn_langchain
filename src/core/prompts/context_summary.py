"""Context-summary prompt builder."""

from langchain_core.messages import HumanMessage, SystemMessage

CONTEXT_SUMMARY_PROMPT_VERSION = "v1"


def build_context_summary_messages(
    *,
    source: str,
    previous_summary: str,
    memory_context: str,
    summary_max_chars: int,
) -> list:
    """Build a summary request while keeping prompt policy out of orchestration."""
    system_content = (
        "You are a practical coding and chat assistant performing context management. "
        "Compress older conversation history from a coding agent session into a compact "
        "structured summary. The content below is real agent-user conversation that "
        "needs to be condensed for context-window efficiency.\n\n"
        + (f"Relevant long-term memory:\n{memory_context}\n\n" if memory_context else "")
        + "Rules:\n"
        "- Preserve concrete facts, user decisions, file paths, current architecture, "
        "open issues, user preferences, and constraints.\n"
        "- Drop transient wording, redundant tool output, inspected file contents, and "
        "generic conversation filler.\n"
        "- Never include secrets, API keys, passwords, tokens, or .env values.\n"
        "- Output concise Markdown with sections.\n"
        "- If the prior summary is empty, start fresh from the messages below."
        + (f"\n\nPrevious summary:\n{previous_summary}" if previous_summary else "")
    )
    return [
        SystemMessage(content=system_content),
        HumanMessage(
            content=(
                f"Older messages to compress:\n{source}\n\n"
                f"Return an updated summary under {summary_max_chars} characters."
            )
        ),
    ]
