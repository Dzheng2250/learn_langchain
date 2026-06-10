import json
import re
from datetime import datetime
from uuid import uuid4

from langchain_core.messages import SystemMessage, messages_from_dict, messages_to_dict

from src.core.config.settings import (
    MEMORY_DB_HOST,
    MEMORY_DB_NAME,
    MEMORY_DB_PASSWORD,
    MEMORY_DB_PORT,
    MEMORY_DB_USER,
    MEMORY_EXTRACTION_ENABLED,
    MEMORY_MIN_IMPORTANCE,
    MEMORY_RETRIEVAL_LIMIT,
)
from src.core.common.debug import debug_print
from src.core.context.manager import AgentContextState
from src.core.database.queries import (
    INSERT_AGENT_MEMORY,
    INSERT_AGENT_MESSAGE,
    SELECT_RECENT_IMPORTANT_MEMORIES,
    SELECT_RELEVANT_MEMORIES,
    SELECT_SESSION_CONTEXT,
    SELECT_SIMILAR_MEMORY_ID,
    UPDATE_AGENT_MEMORY,
    UPSERT_SESSION_CONTEXT,
    execute_sql_file,
)
from src.core.hooks.events import emit_event, record_error, record_memory_saved
from src.core.memory.errors import MemoryUnavailableError
from src.core.memory.extractor import MemoryCandidateExtractor
from src.core.memory.models import RetrievedMemory


MEMORY_MESSAGE_PREFIX = "Relevant long-term memory:"


class PostgresMemoryStore:
    """PostgreSQL-backed conversation archive and memory store."""

    def __init__(
        self,
        host: str = MEMORY_DB_HOST,
        port: int = MEMORY_DB_PORT,
        dbname: str = MEMORY_DB_NAME,
        user: str = MEMORY_DB_USER,
        password: str = MEMORY_DB_PASSWORD,
        retrieval_limit: int = MEMORY_RETRIEVAL_LIMIT,
        min_importance: int = MEMORY_MIN_IMPORTANCE,
    ) -> None:
        self.host = host
        self.port = port
        self.dbname = dbname
        self.user = user
        self.password = password
        self.retrieval_limit = retrieval_limit
        self.min_importance = min_importance
        self.extractor = MemoryCandidateExtractor()
        self._pool = self._load_pool()
        self._Jsonb = self._load_jsonb_adapter()

    def initialize(self) -> None:
        """Create required tables and indexes if they do not exist."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                execute_sql_file(cur, "schema.sql")
            conn.commit()

    def load_session(self, session_id: str) -> tuple[AgentContextState, int]:
        """Load compact context state and current turn index."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(SELECT_SESSION_CONTEXT, (session_id,))
                row = cur.fetchone()

        if not row:
            debug_print("MEMORY SESSION LOAD", f"session_id={session_id}, new session")
            emit_event(
                "context_loaded",
                "agent_memory",
                "No persisted session context found.",
                {"session_id": session_id, "turn_index": 0, "recent_messages": 0},
            )
            return AgentContextState(), 0

        summary, recent_messages_json, turn_index = row
        recent_messages = self._messages_from_json(recent_messages_json)
        debug_print(
            "MEMORY SESSION LOAD",
            f"session_id={session_id}, turn_index={turn_index}, recent_messages={len(recent_messages)}",
        )
        emit_event(
            "context_loaded",
            "agent_memory",
            "Loaded persisted session context.",
            {
                "session_id": session_id,
                "turn_index": turn_index,
                "summary_chars": len(summary or ""),
                "recent_messages": len(recent_messages),
            },
        )
        return AgentContextState(summary=summary or "", recent_messages=recent_messages), turn_index

    def save_session(self, session_id: str, state: AgentContextState, turn_index: int) -> None:
        """Persist compact context state for restart recovery."""
        recent_messages_json = self._messages_to_json(state.recent_messages)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    UPSERT_SESSION_CONTEXT,
                    (
                        session_id,
                        state.summary,
                        self._json_param(recent_messages_json),
                        turn_index,
                    ),
                )
            conn.commit()

        debug_print(
            "MEMORY SESSION SAVE",
            f"session_id={session_id}, turn_index={turn_index}, recent_messages={len(state.recent_messages)}",
        )
        emit_event(
            "context_saved",
            "agent_memory",
            "Saved compact session context.",
            {
                "session_id": session_id,
                "turn_index": turn_index,
                "summary_chars": len(state.summary),
                "recent_messages": len(state.recent_messages),
            },
        )

    def archive_turn_messages(self, session_id: str, turn_index: int, messages: list) -> list[int]:
        """Append all graph messages from one turn to the durable message log."""
        message_ids = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                for message in messages:
                    raw = self._message_to_raw(message)
                    cur.execute(
                        INSERT_AGENT_MESSAGE,
                        (
                            session_id,
                            self._message_role(message),
                            self._message_content(message),
                            message.__class__.__name__,
                            self._json_param(raw),
                            turn_index,
                        ),
                    )
                    message_ids.append(cur.fetchone()[0])
            conn.commit()

        debug_print(
            "MEMORY MESSAGE ARCHIVE",
            f"session_id={session_id}, turn_index={turn_index}, messages={len(message_ids)}",
        )
        emit_event(
            "messages_archived",
            "agent_memory",
            "Archived turn messages.",
            {
                "session_id": session_id,
                "turn_index": turn_index,
                "message_count": len(message_ids),
                "message_ids": message_ids,
            },
        )
        return message_ids

    def retrieve_memories(self, query: str, scope: str = "project") -> list[RetrievedMemory]:
        """Retrieve a small set of relevant long-term memories."""
        normalized_query = self._normalize_search_query(query)
        if not normalized_query:
            return self._recent_important_memories(scope)

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    SELECT_RELEVANT_MEMORIES,
                    (
                        scope,
                        normalized_query,
                        f"%{query[:120]}%",
                        normalized_query,
                        self.retrieval_limit,
                    ),
                )
                rows = cur.fetchall()

        memories = [self._memory_from_row(row) for row in rows]
        debug_print("MEMORY RETRIEVE", self.format_memories(memories))
        emit_event(
            "memory_retrieved",
            "agent_memory",
            "Retrieved long-term memories.",
            {
                "query": query,
                "scope": scope,
                "memory_count": len(memories),
                "memory_ids": [memory.id for memory in memories],
            },
        )
        return memories

    def extract_and_save_memories(
        self,
        session_id: str,
        turn_index: int,
        messages: list,
        source_message_ids: list[int],
        scope: str = "project",
    ) -> list[str]:
        """Extract stable long-term memories from one completed turn."""
        if not MEMORY_EXTRACTION_ENABLED:
            emit_event(
                "memory_extract_skipped",
                "agent_memory",
                "Memory extraction is disabled.",
                {"session_id": session_id, "turn_index": turn_index, "reason": "disabled"},
            )
            return []

        source = self.extractor.format_messages(messages)
        if not source.strip():
            emit_event(
                "memory_extract_skipped",
                "agent_memory",
                "No source messages for memory extraction.",
                {"session_id": session_id, "turn_index": turn_index, "reason": "empty_source"},
            )
            return []

        emit_event(
            "memory_extract_triggered",
            "agent_memory",
            "Started long-term memory extraction.",
            {
                "session_id": session_id,
                "turn_index": turn_index,
                "message_count": len(messages),
                "source_message_ids": source_message_ids,
                "source_chars": len(source),
            },
        )

        try:
            candidates = self.extractor.extract(source)
        except Exception as exc:
            record_error(
                "agent_memory",
                "memory",
                exc,
                "Long-term memory extraction failed.",
                {
                    "session_id": session_id,
                    "turn_index": turn_index,
                },
                event_type="memory_failed",
            )
            raise

        if not candidates:
            debug_print("MEMORY EXTRACT", "No long-term memories extracted.")
            emit_event(
                "memory_extract_finished",
                "agent_memory",
                "No long-term memories extracted.",
                {
                    "session_id": session_id,
                    "turn_index": turn_index,
                    "candidate_count": 0,
                    "saved_count": 0,
                },
            )
            return []

        saved = []
        saved_events = []
        skipped = []
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    for candidate in candidates:
                        importance = int(candidate.get("importance", 3))
                        if importance < self.min_importance:
                            skipped_item = {
                                "reason": "low_importance",
                                "importance": importance,
                                "content_preview": str(candidate.get("content", ""))[:120],
                            }
                            skipped.append(skipped_item)
                            emit_event(
                                "memory_skipped",
                                "agent_memory",
                                "Skipped memory candidate.",
                                skipped_item,
                            )
                            continue

                        content = str(candidate.get("content", "")).strip()
                        if not content:
                            skipped_item = {"reason": "empty_content"}
                            skipped.append(skipped_item)
                            emit_event(
                                "memory_skipped",
                                "agent_memory",
                                "Skipped memory candidate.",
                                skipped_item,
                            )
                            continue
                        if self.extractor.looks_sensitive(content):
                            skipped_item = {
                                "reason": "sensitive_content",
                                "content_preview": content[:120],
                            }
                            skipped.append(skipped_item)
                            emit_event(
                                "memory_skipped",
                                "agent_memory",
                                "Skipped memory candidate.",
                                skipped_item,
                            )
                            continue

                        kind = str(candidate.get("kind", "project_fact")).strip() or "project_fact"
                        tags = candidate.get("tags") if isinstance(candidate.get("tags"), list) else []
                        confidence = float(candidate.get("confidence", 0.8))

                        existing_id = self._find_similar_memory_id(cur, scope, kind, content)
                        if existing_id:
                            cur.execute(
                                UPDATE_AGENT_MEMORY,
                                (
                                    content,
                                    self._json_param(tags),
                                    importance,
                                    confidence,
                                    self._json_param(source_message_ids),
                                    existing_id,
                                ),
                            )
                            saved_item = f"updated {existing_id}: {kind}: {content[:120]}"
                            saved.append(saved_item)
                            saved_events.append(
                                {
                                    "memory_id": existing_id,
                                    "action": "updated",
                                    "kind": kind,
                                    "importance": importance,
                                    "content": content,
                                    "message": "Updated long-term memory.",
                                }
                            )
                        else:
                            memory_id = str(uuid4())
                            cur.execute(
                                INSERT_AGENT_MEMORY,
                                (
                                    memory_id,
                                    scope,
                                    kind,
                                    content,
                                    self._json_param(tags),
                                    importance,
                                    confidence,
                                    self._json_param(source_message_ids),
                                ),
                            )
                            saved_item = f"created {memory_id}: {kind}: {content[:120]}"
                            saved.append(saved_item)
                            saved_events.append(
                                {
                                    "memory_id": memory_id,
                                    "action": "created",
                                    "kind": kind,
                                    "importance": importance,
                                    "content": content,
                                    "message": "Created long-term memory.",
                                }
                            )
                conn.commit()
        except Exception as exc:
            record_error(
                "agent_memory",
                "memory_save",
                exc,
                "Long-term memory database transaction failed.",
                {
                    "session_id": session_id,
                    "turn_index": turn_index,
                    "candidate_count": len(candidates),
                    "pending_save_count": len(saved_events),
                },
                event_type="memory_failed",
            )
            raise

        for saved_event in saved_events:
            record_memory_saved("agent_memory", **saved_event)

        debug_print("MEMORY SAVE LONG TERM", "\n".join(saved) or "No memories saved.")
        if skipped:
            debug_print("MEMORY SAVE SKIPPED", json.dumps(skipped, ensure_ascii=False, indent=2))
        emit_event(
            "memory_extract_finished",
            "agent_memory",
            "Finished long-term memory extraction.",
            {
                "session_id": session_id,
                "turn_index": turn_index,
                "candidate_count": len(candidates),
                "saved_count": len(saved),
                "skipped_count": len(skipped),
            },
        )
        return saved

    def build_memory_message(self, memories: list[RetrievedMemory]) -> SystemMessage | None:
        """Build a system message containing retrieved long-term memories."""
        if not memories:
            return None
        return SystemMessage(content=f"{MEMORY_MESSAGE_PREFIX}\n{self.format_memories(memories)}")

    def format_memories(self, memories: list[RetrievedMemory]) -> str:
        """Format retrieved memory records for LLM context or debug output."""
        if not memories:
            return "(none)"
        lines = []
        for memory in memories:
            tag_text = ", ".join(memory.tags) if memory.tags else "no-tags"
            lines.append(
                f"- [{memory.kind} | {memory.scope} | importance={memory.importance} | "
                f"tags={tag_text}] {memory.content}"
            )
        return "\n".join(lines)

    def _connect(self):
        """Return a connection context manager from the pool."""
        try:
            return self._pool.connection()
        except Exception as exc:
            raise MemoryUnavailableError(
                "Cannot connect to PostgreSQL memory database. "
                f"Expected database '{self.dbname}' on {self.host}:{self.port}. "
                "Create it first with: createdb -U postgres learn_agent"
            ) from exc

    def close(self):
        """Close the connection pool, releasing all connections."""
        self._pool.close()

    def _load_pool(self):
        """Lazy-import psycopg_pool and create a ConnectionPool."""
        try:
            from psycopg.conninfo import make_conninfo
            from psycopg_pool import ConnectionPool
        except ImportError as exc:
            raise MemoryUnavailableError(
                "Python package 'psycopg_pool' is required for connection pooling. "
                "Install it in the agent_learn environment, for example: "
                "pip install psycopg[pool]"
            ) from exc
        try:
            return ConnectionPool(
                conninfo=make_conninfo(
                    "",
                    host=self.host,
                    port=self.port,
                    dbname=self.dbname,
                    user=self.user,
                    password=self.password,
                ),
                min_size=1,
                max_size=2,
                open=True,
            )
        except Exception as exc:
            raise MemoryUnavailableError(
                "Failed to create PostgreSQL connection pool. "
                f"Expected database '{self.dbname}' on {self.host}:{self.port}."
            ) from exc

    def _load_jsonb_adapter(self):
        try:
            from psycopg.types.json import Jsonb

            return Jsonb
        except ImportError as exc:
            raise MemoryUnavailableError(
                "Python package 'psycopg' is required for PostgreSQL JSONB memory storage."
            ) from exc

    def _json_param(self, value):
        return self._Jsonb(
            value,
            dumps=lambda obj: json.dumps(obj, ensure_ascii=False, default=str),
        )

    def _messages_to_json(self, messages: list) -> list[dict]:
        return messages_to_dict(messages)

    def _messages_from_json(self, value) -> list:
        if not value:
            return []
        if isinstance(value, str):
            value = json.loads(value)
        return messages_from_dict(value)

    def _message_to_raw(self, message) -> dict:
        return messages_to_dict([message])[0]

    def _message_role(self, message) -> str:
        message_type = message.__class__.__name__
        if message_type == "HumanMessage":
            return "user"
        if message_type == "AIMessage":
            return "assistant"
        if message_type == "ToolMessage":
            return "tool"
        if message_type == "SystemMessage":
            return "system"
        return "unknown"

    def _message_content(self, message) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        return json.dumps(content, ensure_ascii=False, default=str)

    def _memory_from_row(self, row) -> RetrievedMemory:
        memory_id, scope, kind, content, tags, importance, confidence = row
        if isinstance(tags, str):
            tags = json.loads(tags)
        return RetrievedMemory(
            id=memory_id,
            scope=scope,
            kind=kind,
            content=content,
            tags=tags or [],
            importance=importance,
            confidence=confidence,
        )

    def _recent_important_memories(self, scope: str) -> list[RetrievedMemory]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(SELECT_RECENT_IMPORTANT_MEMORIES, (scope, self.retrieval_limit))
                rows = cur.fetchall()
        memories = [self._memory_from_row(row) for row in rows]
        debug_print("MEMORY RETRIEVE", self.format_memories(memories))
        emit_event(
            "memory_retrieved",
            "agent_memory",
            "Retrieved recent important memories.",
            {
                "scope": scope,
                "memory_count": len(memories),
                "memory_ids": [memory.id for memory in memories],
            },
        )
        return memories

    def _normalize_search_query(self, query: str) -> str:
        words = re.findall(r"[\w\u4e00-\u9fff]+", query)
        return " ".join(words[:20])

    def _find_similar_memory_id(self, cur, scope: str, kind: str, content: str) -> str | None:
        content_prefix = content[:160]
        cur.execute(SELECT_SIMILAR_MEMORY_ID, (scope, kind, content, content_prefix))
        row = cur.fetchone()
        return row[0] if row else None

