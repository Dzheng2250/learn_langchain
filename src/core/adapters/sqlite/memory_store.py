"""SQLite long-term memory retrieval adapter."""

from __future__ import annotations

import json
import re
from uuid import UUID
from typing import TYPE_CHECKING

from langchain_core.messages import SystemMessage

from src.config.settings import (
    MEMORY_BOOTSTRAP_LIMIT,
    MEMORY_CONTEXT_CHAR_LIMIT,
    MEMORY_RETRIEVAL_LIMIT,
)
from src.core.memory.models import RetrievedMemory
from src.core.telemetry import emit_event

if TYPE_CHECKING:
    from src.core.state.database import LocalStateDatabase


MEMORY_MESSAGE_PREFIX = "Relevant long-term memory for this workspace:"


class SQLiteMemoryRetrievalStore:
    """Retrieve bounded workspace memories for foreground prompt construction."""

    def __init__(
        self,
        database: LocalStateDatabase,
        *,
        retrieval_limit: int = MEMORY_RETRIEVAL_LIMIT,
    ) -> None:
        self.database = database
        self.retrieval_limit = retrieval_limit

    def retrieve_relevant(
        self,
        workspace_id: UUID,
        query: str,
        limit: int | None = None,
    ) -> list[RetrievedMemory]:
        effective_limit = limit or self.retrieval_limit
        terms = self._normalize_search_query(query).casefold().split()
        if not terms:
            return []
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT memory_id, kind, content, tags, importance, confidence
                FROM memories
                WHERE workspace_id = ? AND archived_at IS NULL
                ORDER BY importance DESC, updated_at DESC
                """,
                (str(workspace_id),),
            ).fetchall()
        selected = [
            row
            for row in rows
            if any(term in row["content"].casefold() for term in terms)
        ]
        if not selected and self._is_memory_recall_query(query):
            selected = rows[:effective_limit]
        memories = [self._memory_from_row(row) for row in selected[:effective_limit]]
        self._record_retrieval(workspace_id, query, memories, "relevant")
        return memories

    def retrieve_bootstrap(
        self,
        workspace_id: UUID,
        limit: int = MEMORY_BOOTSTRAP_LIMIT,
    ) -> list[RetrievedMemory]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT memory_id, kind, content, tags, importance, confidence
                FROM memories
                WHERE workspace_id = ? AND archived_at IS NULL
                ORDER BY importance DESC, updated_at DESC
                LIMIT ?
                """,
                (str(workspace_id), limit),
            ).fetchall()
        memories = [self._memory_from_row(row) for row in rows]
        self._record_retrieval(workspace_id, "", memories, "bootstrap")
        return memories

    def retrieve_for_turn(
        self,
        workspace_id: UUID,
        query: str,
        *,
        new_session: bool,
    ) -> list[RetrievedMemory]:
        combined = []
        if new_session:
            combined.extend(self.retrieve_bootstrap(workspace_id))
        combined.extend(self.retrieve_relevant(workspace_id, query))
        unique, seen, chars = [], set(), 0
        for memory in combined:
            if memory.id in seen:
                continue
            if len(unique) >= self.retrieval_limit:
                break
            if unique and chars + len(memory.content) > MEMORY_CONTEXT_CHAR_LIMIT:
                break
            seen.add(memory.id)
            chars += len(memory.content)
            unique.append(memory)
        return unique

    def build_memory_message(self, memories: list[RetrievedMemory]) -> SystemMessage | None:
        if not memories:
            return None
        return SystemMessage(content=f"{MEMORY_MESSAGE_PREFIX}\n{self.format_memories(memories)}")

    def format_memories(self, memories: list[RetrievedMemory]) -> str:
        return "\n".join(
            f"- [{memory.kind} | importance={memory.importance}] {memory.content}"
            for memory in memories
        ) or "(none)"

    def _memory_from_row(self, row) -> RetrievedMemory:
        return RetrievedMemory(
            id=row["memory_id"],
            kind=row["kind"],
            content=row["content"],
            tags=json.loads(row["tags"] or "[]"),
            importance=int(row["importance"]),
            confidence=float(row["confidence"]),
        )

    def _normalize_search_query(self, query: str) -> str:
        return " ".join(re.findall(r"[\w\u4e00-\u9fff]+", query)[:20])

    def _is_memory_recall_query(self, query: str) -> bool:
        lowered = query.casefold()
        return any(term in lowered for term in ("记得", "记忆", "之前", "以前", "remember", "memory"))

    def _record_retrieval(
        self,
        workspace_id: UUID,
        query: str,
        memories: list[RetrievedMemory],
        kind: str,
    ) -> None:
        emit_event(
            "memory_retrieved",
            "agent_memory",
            "Retrieved workspace long-term memories.",
            {
                "workspace_id": str(workspace_id),
                "query": query,
                "retrieval_type": kind,
                "memory_count": len(memories),
                "memory_ids": [memory.id for memory in memories],
            },
        )
