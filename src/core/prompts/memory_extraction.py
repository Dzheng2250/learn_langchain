"""Long-term memory extraction prompt."""

from langchain_core.messages import HumanMessage, SystemMessage

MEMORY_EXTRACTION_PROMPT_VERSION = "v1"
MEMORY_EXTRACTION_SYSTEM_PROMPT = (
    "Extract durable long-term memories for a local coding agent. "
    "Return strict JSON only: an array of objects with keys "
    "kind, content, tags, importance, confidence. "
    "Only include stable user preferences, project facts, architecture "
    "decisions, task state, or reusable troubleshooting notes. "
    "If the user explicitly asks to remember something, extract the "
    "thing they asked to remember as a durable memory unless it is "
    "sensitive or unsafe. "
    "Do not include secrets, API keys, passwords, .env values, transient "
    "tool output, or generic conversation filler. "
    "If nothing should be remembered, return []."
)


def build_memory_extraction_messages(source: str) -> list:
    """Build the stable model input for extracting memory candidates."""
    return [
        SystemMessage(content=MEMORY_EXTRACTION_SYSTEM_PROMPT),
        HumanMessage(content=f"Conversation turn:\n{source}"),
    ]
