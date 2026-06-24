"""Retrieval helpers for workspace long-term memory."""

import re
from uuid import UUID

from src.core.memory.models import RetrievedMemory
from src.core.telemetry import emit_event


def normalize_search_query(query: str) -> str:
    """Build a bounded simple-text query for PostgreSQL full-text search."""
    return " ".join(re.findall(r"[\w\u4e00-\u9fff]+", query)[:20])


def is_memory_recall_query(query: str) -> bool:
    """Detect explicit recall wording that enables important-memory fallback."""
    lowered = query.casefold()
    return any(term in lowered for term in ("记得", "记忆", "之前", "以前", "remember", "memory"))


def record_retrieval(
    workspace_id: UUID,
    query: str,
    memories: list[RetrievedMemory],
    retrieval_type: str,
) -> None:
    """Emit bounded observation metadata for one retrieval operation."""
    emit_event(
        "memory_retrieved",
        "agent_memory",
        "Retrieved workspace long-term memories.",
        {
            "workspace_id": str(workspace_id),
            "query": query,
            "retrieval_type": retrieval_type,
            "memory_count": len(memories),
            "memory_ids": [memory.id for memory in memories],
        },
    )
