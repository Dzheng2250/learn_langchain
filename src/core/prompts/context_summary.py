"""Context-summary prompt builder."""

from langchain_core.messages import HumanMessage, SystemMessage

CONTEXT_SUMMARY_PROMPT_VERSION = "v2"


def build_context_summary_messages(
    *,
    source: str,
    previous_summary: str,
    memory_context: str,
    summary_max_tokens: int,
    phase: str = "final",
) -> list:
    """Build a summary request while keeping prompt policy out of orchestration."""
    system_content = (
        "You are a practical coding and chat assistant performing context management. "
        "Compress older conversation history from a coding agent session into a compact "
        "structured summary. The content below is real agent-user conversation that "
        "needs to be condensed for context-window efficiency.\n\n"
        + "Rules:\n"
        "- Preserve concrete facts, user decisions, file paths, current architecture, "
        "open issues, user preferences, and constraints.\n"
        "- Drop transient wording, redundant tool output, inspected file contents, and "
        "generic conversation filler.\n"
        "- Never include secrets, API keys, passwords, tokens, or .env values.\n"
        "- Output concise Markdown with sections.\n"
        "- If the prior summary is empty, start fresh from the messages below.\n"
        "- Never omit unresolved work, failures, constraints, or user decisions merely "
        "to shorten the summary."
    )
    phase_instruction = (
        "Create an intermediate summary of this source segment for a later merge."
        if phase == "map"
        else "Create the complete updated session summary."
    )
    return [
        SystemMessage(content=system_content),
        HumanMessage(
            content=(
                f"Task: {phase_instruction}\n"
                f"Maximum output budget: {summary_max_tokens} tokens.\n\n"
                + (f"Previous summary:\n{previous_summary}\n\n" if previous_summary else "")
                + (f"Relevant long-term memory:\n{memory_context}\n\n" if memory_context else "")
                + f"Older messages to compress:\n{source}"
            )
        ),
    ]
