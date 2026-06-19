"""Formatting helpers for long-term memory model input."""

import json

from langchain_core.messages import SystemMessage

from src.core.memory.models import RetrievedMemory


MEMORY_MESSAGE_PREFIX = "Relevant long-term memory for this workspace:"


def memory_from_row(row) -> RetrievedMemory:
    """Convert one repository row into a typed retrieved memory."""
    memory_id, kind, content, tags, importance, confidence = row
    if isinstance(tags, str):
        tags = json.loads(tags)
    return RetrievedMemory(
        id=memory_id,
        kind=kind,
        content=content,
        tags=tags or [],
        importance=importance,
        confidence=confidence,
    )


def format_memories(memories: list[RetrievedMemory]) -> str:
    """Format memories for model input while retaining kind and importance."""
    return "\n".join(
        f"- [{memory.kind} | importance={memory.importance}] {memory.content}" for memory in memories
    ) or "(none)"


def build_memory_message(memories: list[RetrievedMemory]) -> SystemMessage | None:
    """Convert retrieved memories into one synthetic input-only message."""
    if not memories:
        return None
    return SystemMessage(content=f"{MEMORY_MESSAGE_PREFIX}\n{format_memories(memories)}")
