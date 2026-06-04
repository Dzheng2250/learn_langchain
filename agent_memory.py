import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage, messages_from_dict, messages_to_dict
from langchain_openai import ChatOpenAI

from agent_config import (
    MEMORY_DB_HOST,
    MEMORY_DB_NAME,
    MEMORY_DB_PASSWORD,
    MEMORY_DB_PORT,
    MEMORY_DB_USER,
    MEMORY_EXTRACT_SOURCE_CHAR_LIMIT,
    MEMORY_EXTRACTION_ENABLED,
    MEMORY_MIN_IMPORTANCE,
    MEMORY_RETRIEVAL_LIMIT,
    MODEL,
)
from agent_context import AgentContextState
from agent_debug import debug_print, format_message
from agent_hooks import emit_event, event_span, record_error, record_memory_saved


MEMORY_MESSAGE_PREFIX = "Relevant long-term memory:"


@dataclass
class RetrievedMemory:
    """One long-term memory record selected for the current user turn."""

    id: str
    scope: str
    kind: str
    content: str
    tags: list[str] = field(default_factory=list)
    importance: int = 3
    confidence: float = 1.0


class MemoryUnavailableError(RuntimeError):
    """Raised when the configured memory backend cannot be used."""


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
        self._psycopg = self._load_psycopg()
        self._Jsonb = self._load_jsonb_adapter()

    def initialize(self) -> None:
        """Create required tables and indexes if they do not exist."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_sessions (
                        session_id TEXT PRIMARY KEY,
                        summary TEXT NOT NULL DEFAULT '',
                        recent_messages JSONB NOT NULL DEFAULT '[]',
                        turn_index INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_messages (
                        id BIGSERIAL PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL DEFAULT '',
                        message_type TEXT NOT NULL,
                        raw JSONB NOT NULL DEFAULT '{}',
                        turn_index INTEGER NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_memories (
                        id UUID PRIMARY KEY,
                        scope TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        content TEXT NOT NULL,
                        tags JSONB NOT NULL DEFAULT '[]',
                        importance INTEGER NOT NULL DEFAULT 3,
                        confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                        source_message_ids JSONB NOT NULL DEFAULT '[]',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        archived_at TIMESTAMPTZ
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_events (
                        id BIGSERIAL PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        turn_index INTEGER,
                        event_type TEXT NOT NULL,
                        source TEXT NOT NULL,
                        level TEXT NOT NULL DEFAULT 'info',
                        message TEXT NOT NULL DEFAULT '',
                        payload JSONB NOT NULL DEFAULT '{}',
                        duration_ms INTEGER,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agent_messages_session "
                    "ON agent_messages(session_id, turn_index, id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agent_memories_scope "
                    "ON agent_memories(scope)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agent_memories_kind "
                    "ON agent_memories(kind)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agent_memories_importance "
                    "ON agent_memories(importance DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agent_memories_content_tsv "
                    "ON agent_memories "
                    "USING GIN (to_tsvector('simple', content))"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agent_events_session_turn "
                    "ON agent_events(session_id, turn_index, id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agent_events_run "
                    "ON agent_events(run_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agent_events_type "
                    "ON agent_events(event_type)"
                )
            conn.commit()

    def load_session(self, session_id: str) -> tuple[AgentContextState, int]:
        """Load compact context state and current turn index."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT summary, recent_messages, turn_index
                    FROM agent_sessions
                    WHERE session_id = %s
                    """,
                    (session_id,),
                )
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
                    """
                    INSERT INTO agent_sessions (
                        session_id, summary, recent_messages, turn_index, updated_at
                    )
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (session_id) DO UPDATE SET
                        summary = EXCLUDED.summary,
                        recent_messages = EXCLUDED.recent_messages,
                        turn_index = EXCLUDED.turn_index,
                        updated_at = now()
                    """,
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
                        """
                        INSERT INTO agent_messages (
                            session_id, role, content, message_type, raw, turn_index
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
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
                    """
                    SELECT id::text, scope, kind, content, tags, importance, confidence
                    FROM agent_memories
                    WHERE archived_at IS NULL
                      AND scope IN (%s, 'global')
                      AND (
                          to_tsvector('simple', content) @@ plainto_tsquery('simple', %s)
                          OR content ILIKE %s
                      )
                    ORDER BY
                        ts_rank(to_tsvector('simple', content), plainto_tsquery('simple', %s)) DESC,
                        importance DESC,
                        updated_at DESC
                    LIMIT %s
                    """,
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

        source = self._format_messages_for_extraction(messages)
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
            candidates = self._extract_memory_candidates(source)
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
        skipped = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                for candidate in candidates:
                    importance = int(candidate.get("importance", 3))
                    if importance < self.min_importance:
                        reason = "low_importance"
                        skipped_item = {
                            "reason": reason,
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
                    if self._looks_sensitive(content):
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
                            """
                            UPDATE agent_memories
                            SET content = %s,
                                tags = %s,
                                importance = GREATEST(importance, %s),
                                confidence = GREATEST(confidence, %s),
                                source_message_ids = %s,
                                updated_at = now()
                            WHERE id = %s
                            """,
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
                        record_memory_saved(
                            "agent_memory",
                            memory_id=existing_id,
                            action="updated",
                            kind=kind,
                            importance=importance,
                            content=content,
                            message="Updated long-term memory.",
                        )
                    else:
                        memory_id = str(uuid4())
                        cur.execute(
                            """
                            INSERT INTO agent_memories (
                                id, scope, kind, content, tags, importance,
                                confidence, source_message_ids
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            """,
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
                        record_memory_saved(
                            "agent_memory",
                            memory_id=memory_id,
                            action="created",
                            kind=kind,
                            importance=importance,
                            content=content,
                            message="Created long-term memory.",
                        )
            conn.commit()

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
        try:
            return self._psycopg.connect(
                host=self.host,
                port=self.port,
                dbname=self.dbname,
                user=self.user,
                password=self.password,
            )
        except Exception as exc:
            raise MemoryUnavailableError(
                "Cannot connect to PostgreSQL memory database. "
                f"Expected database '{self.dbname}' on {self.host}:{self.port}. "
                "Create it first with: createdb -U postgres learn_agent"
            ) from exc

    def _load_psycopg(self):
        try:
            import psycopg

            return psycopg
        except ImportError as exc:
            raise MemoryUnavailableError(
                "Python package 'psycopg' is required for PostgreSQL memory. "
                "Install it in the agent_learn environment, for example: "
                "pip install psycopg[binary]"
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
                cur.execute(
                    """
                    SELECT id::text, scope, kind, content, tags, importance, confidence
                    FROM agent_memories
                    WHERE archived_at IS NULL
                      AND scope IN (%s, 'global')
                    ORDER BY importance DESC, updated_at DESC
                    LIMIT %s
                    """,
                    (scope, self.retrieval_limit),
                )
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

    def _format_messages_for_extraction(self, messages: list) -> str:
        formatted = []
        for index, message in enumerate(messages, start=1):
            text = format_message(message)
            if len(text) > 1200:
                text = text[:1200] + "\n... message truncated ..."
            formatted.append(f"[{index}]\n{text}")

        source = "\n\n".join(formatted)
        if len(source) > MEMORY_EXTRACT_SOURCE_CHAR_LIMIT:
            source = source[-MEMORY_EXTRACT_SOURCE_CHAR_LIMIT:]
        return source

    def _extract_memory_candidates(self, source: str) -> list[dict]:
        llm = self._create_memory_llm()
        with event_span(
            "memory_candidate_extract",
            "agent_memory",
            payload={"source_chars": len(source)},
        ):
            response = llm.invoke(
                [
                    SystemMessage(
                        content=(
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
                    ),
                    HumanMessage(content=f"Conversation turn:\n{source}"),
                ]
            )

        content = str(response.content).strip()
        try:
            parsed = json.loads(self._strip_json_fence(content))
        except json.JSONDecodeError:
            debug_print("MEMORY EXTRACT PARSE FAILED", content)
            return []

        if not isinstance(parsed, list):
            return []

        debug_print("MEMORY EXTRACT", json.dumps(parsed, ensure_ascii=False, indent=2))
        return [item for item in parsed if isinstance(item, dict)]

    def _strip_json_fence(self, content: str) -> str:
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        return content.strip()

    def _create_memory_llm(self) -> ChatOpenAI:
        load_dotenv()
        return ChatOpenAI(
            model=MODEL,
            api_key=os.getenv("ALIYUN_API_KEY"),
            base_url=os.getenv("ALIYUN_BASE_URL"),
            temperature=0,
            streaming=False,
        )

    def _find_similar_memory_id(self, cur, scope: str, kind: str, content: str) -> str | None:
        content_prefix = content[:160]
        cur.execute(
            """
            SELECT id::text
            FROM agent_memories
            WHERE archived_at IS NULL
              AND scope = %s
              AND kind = %s
              AND (
                  content = %s
                  OR left(content, 160) = %s
              )
            LIMIT 1
            """,
            (scope, kind, content, content_prefix),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def _looks_sensitive(self, content: str) -> bool:
        lower = content.lower()
        sensitive_terms = [
            "api_key",
            "apikey",
            "password",
            "passwd",
            "secret",
            "token",
            ".env",
            "authorization",
        ]
        return any(term in lower for term in sensitive_terms)
