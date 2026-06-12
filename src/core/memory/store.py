"""Workspace-aware PostgreSQL conversation and long-term memory facade."""

import json
import re
from uuid import UUID, uuid4

from langchain_core.messages import SystemMessage, messages_from_dict, messages_to_dict

from src.config.settings import (
    MEMORY_BOOTSTRAP_LIMIT,
    MEMORY_CONTEXT_CHAR_LIMIT,
    MEMORY_EXTRACTION_ENABLED,
    MEMORY_MIN_IMPORTANCE,
    MEMORY_RETRIEVAL_LIMIT,
)
from src.core.context.models import AgentContextState
from src.core.database.connection import create_pool
from src.core.database.schema import SchemaManager
from src.core.hooks.events import emit_event, record_error, record_memory_saved
from src.core.memory.extractor import MemoryCandidateExtractor
from src.core.memory.models import RetrievedMemory
from src.core.memory.repositories import MemoryRepository, MessageRepository, SessionRepository
from src.core.llm.provider import ModelProvider
from src.core.workspace.models import SessionContext


MEMORY_MESSAGE_PREFIX = "Relevant long-term memory for this workspace:"


class PostgresMemoryStore:
    """Facade over workspace-aware session, message, and memory persistence."""

    def __init__(
        self,
        *,
        pool=None,
        retrieval_limit: int = MEMORY_RETRIEVAL_LIMIT,
        min_importance: int = MEMORY_MIN_IMPORTANCE,
        model_provider: ModelProvider | None = None,
    ) -> None:
        self._pool = pool or create_pool()
        self._owns_pool = pool is None
        self.retrieval_limit = retrieval_limit
        self.min_importance = min_importance
        self.extractor = MemoryCandidateExtractor(model_provider)
        self.sessions = SessionRepository(self._pool)
        self.messages = MessageRepository(self._pool)
        self.memories = MemoryRepository(self._pool)
        from psycopg.types.json import Jsonb

        self._Jsonb = Jsonb

    def initialize(self) -> None:
        """Create or upgrade the database schema required by the memory facade."""
        SchemaManager(self._pool).initialize()

    def load_session(self, session: SessionContext) -> tuple[AgentContextState, int]:
        """Load compact context and the last completed Turn index."""
        row = self.sessions.load(session)
        if not row:
            raise RuntimeError("Resolved session disappeared before it could be loaded.")
        summary, recent, turn_index = row
        return AgentContextState(summary=summary or "", recent_messages=self._messages_from_json(recent)), turn_index

    def save_session(self, session: SessionContext, state: AgentContextState, turn_index: int) -> None:
        """Persist compact context after a successfully completed Turn."""
        self.sessions.update(
            session,
            state.summary,
            self._json_param(messages_to_dict(state.recent_messages)),
            turn_index,
        )

    def archive_turn_messages(self, session: SessionContext, turn_index: int, messages: list) -> list[int]:
        """Append full Turn messages and return IDs for memory-source tracking."""
        rows = [
            (
                self._message_role(message),
                self._message_content(message),
                message.__class__.__name__,
                self._json_param(messages_to_dict([message])[0]),
            )
            for message in messages
        ]
        return self.messages.append(session, turn_index, rows)

    def retrieve_relevant(self, workspace_id: UUID, query: str, limit: int | None = None) -> list[RetrievedMemory]:
        """Retrieve query-related memories without crossing Workspace boundaries."""
        normalized = self._normalize_search_query(query)
        if not normalized:
            return []
        rows = self.memories.relevant(workspace_id, normalized, query, limit or self.retrieval_limit)
        if not rows and self._is_memory_recall_query(query):
            rows = self.memories.recent_important(workspace_id, limit or self.retrieval_limit)
        memories = [self._memory_from_row(row) for row in rows]
        self._record_retrieval(workspace_id, query, memories, "relevant")
        return memories

    def retrieve_bootstrap(self, workspace_id: UUID, limit: int = MEMORY_BOOTSTRAP_LIMIT) -> list[RetrievedMemory]:
        """Retrieve important recent memories for the first real Session Turn."""
        rows = self.memories.recent_important(workspace_id, limit)
        memories = [self._memory_from_row(row) for row in rows]
        self._record_retrieval(workspace_id, "", memories, "bootstrap")
        return memories

    def retrieve_for_turn(self, workspace_id: UUID, query: str, *, new_session: bool) -> list[RetrievedMemory]:
        """Return bounded, deduplicated memory input for one workspace turn."""
        combined = []
        if new_session:
            combined.extend(self.retrieve_bootstrap(workspace_id))
        combined.extend(self.retrieve_relevant(workspace_id, query))
        unique = []
        seen = set()
        chars = 0
        for memory in combined:
            if memory.id in seen:
                continue
            if unique and chars + len(memory.content) > MEMORY_CONTEXT_CHAR_LIMIT:
                break
            seen.add(memory.id)
            chars += len(memory.content)
            unique.append(memory)
        return unique

    def extract_and_save_memories(
        self,
        session: SessionContext,
        turn_index: int,
        messages: list,
        source_message_ids: list[int],
    ) -> list[str]:
        """Extract, deduplicate, persist, and source-link durable memories."""
        if not MEMORY_EXTRACTION_ENABLED:
            return []
        source = self.extractor.format_messages(messages)
        if not source.strip():
            return []
        try:
            candidates = self.extractor.extract(source)
        except Exception as exc:
            record_error("agent_memory", "memory", exc, "Long-term memory extraction failed.")
            raise

        saved = []
        events = []
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    for candidate in candidates:
                        content = str(candidate.get("content", "")).strip()
                        importance = int(candidate.get("importance", 3))
                        if not content or importance < self.min_importance or self.extractor.looks_sensitive(content):
                            continue
                        kind = str(candidate.get("kind", "project_fact")).strip() or "project_fact"
                        tags = candidate.get("tags") if isinstance(candidate.get("tags"), list) else []
                        confidence = float(candidate.get("confidence", 0.8))
                        existing = self.memories.find_similar(
                            cur,
                            session.workspace.workspace_id,
                            kind,
                            content,
                        )
                        if existing:
                            self.memories.update(
                                cur,
                                session.workspace.workspace_id,
                                existing,
                                content,
                                self._json_param(tags),
                                importance,
                                confidence,
                            )
                            memory_id = existing
                            action = "updated"
                        else:
                            memory_id = str(uuid4())
                            self.memories.insert(
                                cur,
                                memory_id,
                                session.workspace.workspace_id,
                                kind,
                                content,
                                self._json_param(tags),
                                importance,
                                confidence,
                            )
                            action = "created"
                        # Sources describe the deterministic message set supplied
                        # to this extraction run, not LLM-selected evidence.
                        self.memories.add_sources(
                            cur,
                            session.workspace.workspace_id,
                            memory_id,
                            source_message_ids,
                        )
                        saved.append(f"{action} {memory_id}: {kind}: {content[:120]}")
                        events.append(
                            {
                                "memory_id": memory_id,
                                "action": action,
                                "kind": kind,
                                "importance": importance,
                                "content": content,
                                "message": f"{action.title()} long-term memory.",
                            }
                        )
                conn.commit()
        except Exception as exc:
            record_error(
                "agent_memory",
                "memory_save",
                exc,
                "Long-term memory database transaction failed.",
                event_type="memory_failed",
            )
            raise
        for event in events:
            record_memory_saved("agent_memory", **event)
        return saved

    def build_memory_message(self, memories: list[RetrievedMemory]) -> SystemMessage | None:
        """Convert retrieved memories into one synthetic input-only message."""
        if not memories:
            return None
        return SystemMessage(content=f"{MEMORY_MESSAGE_PREFIX}\n{self.format_memories(memories)}")

    def format_memories(self, memories: list[RetrievedMemory]) -> str:
        """Format memories for model input while retaining kind and importance."""
        return "\n".join(
            f"- [{memory.kind} | importance={memory.importance}] {memory.content}" for memory in memories
        ) or "(none)"

    def close(self) -> None:
        """Close only a connection pool owned by this facade instance."""
        if self._owns_pool:
            self._pool.close()

    def _connect(self):
        """Return a pooled connection context manager."""
        return self._pool.connection()

    def _json_param(self, value):
        """Adapt Python values to JSONB without escaping non-ASCII text."""
        return self._Jsonb(value, dumps=lambda obj: json.dumps(obj, ensure_ascii=False, default=str))

    def _messages_from_json(self, value) -> list:
        """Restore LangChain messages from stored JSONB or JSON text."""
        if not value:
            return []
        if isinstance(value, str):
            value = json.loads(value)
        return messages_from_dict(value)

    def _message_role(self, message) -> str:
        """Map LangChain message classes to stable database role names."""
        return {
            "HumanMessage": "user",
            "AIMessage": "assistant",
            "ToolMessage": "tool",
            "SystemMessage": "system",
        }.get(message.__class__.__name__, "unknown")

    def _message_content(self, message) -> str:
        """Return searchable text while preserving structured content as JSON."""
        content = getattr(message, "content", "")
        return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)

    def _memory_from_row(self, row) -> RetrievedMemory:
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

    def _normalize_search_query(self, query: str) -> str:
        """Build a bounded simple-text query for PostgreSQL full-text search."""
        return " ".join(re.findall(r"[\w\u4e00-\u9fff]+", query)[:20])

    def _is_memory_recall_query(self, query: str) -> bool:
        """Detect explicit recall wording that enables important-memory fallback."""
        lowered = query.casefold()
        return any(term in lowered for term in ("记得", "记忆", "之前", "以前", "remember", "memory"))

    def _record_retrieval(
        self,
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
