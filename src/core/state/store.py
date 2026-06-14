"""SQLite-backed Session context, message archive, and long-term memory store."""

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
    POSTGRES_PROJECTION_ENABLED,
)
from src.core.context.models import AgentContextState
from src.core.memory.extractor import MemoryCandidateExtractor
from src.core.memory.models import RetrievedMemory
from src.core.state.database import LocalStateDatabase
from src.core.telemetry import emit_event, record_error, record_memory_saved
from src.core.workspace.models import SessionContext


MEMORY_MESSAGE_PREFIX = "Relevant long-term memory for this workspace:"


class LocalStateStore:
    """Authoritative local facade matching the Agent service persistence contract."""

    def __init__(
        self,
        database: LocalStateDatabase,
        *,
        retrieval_limit: int = MEMORY_RETRIEVAL_LIMIT,
        min_importance: int = MEMORY_MIN_IMPORTANCE,
        model_provider=None,
        projection_enabled: bool = POSTGRES_PROJECTION_ENABLED,
    ) -> None:
        self.database = database
        self.retrieval_limit = retrieval_limit
        self.min_importance = min_importance
        self.extractor = MemoryCandidateExtractor(model_provider)
        self.projection_enabled = projection_enabled

    def initialize(self) -> None:
        self.database.initialize()

    def close(self) -> None:
        """Connections are short-lived, so the facade owns no open resource."""

    def load_session(self, session: SessionContext) -> tuple[AgentContextState, int]:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT summary, recent_messages, turn_index FROM sessions
                WHERE workspace_id = ? AND session_id = ?
                """,
                (str(session.workspace.workspace_id), str(session.session_id)),
            ).fetchone()
        if not row:
            raise RuntimeError("Resolved session disappeared before it could be loaded.")
        recent = json.loads(row["recent_messages"] or "[]")
        return AgentContextState(row["summary"] or "", messages_from_dict(recent)), int(row["turn_index"])

    def save_session(self, session: SessionContext, state: AgentContextState, turn_index: int) -> None:
        with self.database.transaction() as conn:
            self._save_session(conn, session, state, turn_index)

    def archive_turn_messages(self, session: SessionContext, turn_index: int, messages: list) -> list[str]:
        with self.database.transaction() as conn:
            return self._append_messages(conn, session, turn_index, messages)

    def commit_turn(
        self,
        session: SessionContext,
        turn_index: int,
        messages: list,
        state: AgentContextState,
    ) -> list[str]:
        """Atomically append one completed Turn and update compact Session state."""
        with self.database.transaction() as conn:
            ids = self.append_messages_in_transaction(conn, session, turn_index, messages)
            self.save_session_in_transaction(conn, session, state, turn_index)
            self._enqueue_outbox(
                conn,
                "turn_committed",
                "session",
                str(session.session_id),
                {"workspace_id": str(session.workspace.workspace_id), "turn_index": turn_index},
            )
            return ids

    def append_messages_in_transaction(
        self,
        conn,
        session: SessionContext,
        turn_index: int,
        messages: list,
        *,
        execution_id: str | None = None,
    ) -> list[str]:
        """Append messages using a caller-owned Unit of Work transaction."""
        return self._append_messages(
            conn,
            session,
            turn_index,
            messages,
            execution_id=execution_id,
        )

    def save_session_in_transaction(
        self,
        conn,
        session: SessionContext,
        state: AgentContextState,
        turn_index: int,
    ) -> None:
        """Update compact Session state using a caller-owned transaction."""
        self._save_session(conn, session, state, turn_index)

    def save_fast_session_in_transaction(
        self,
        conn,
        session: SessionContext,
        state: AgentContextState,
        turn_index: int,
    ) -> None:
        """Commit recent messages without overwriting a concurrent derived summary."""
        recent = json.dumps(messages_to_dict(state.recent_messages), ensure_ascii=False, default=str)
        cur = conn.execute(
            """
            UPDATE sessions SET recent_messages=?, turn_index=?,
                version=version + 1, updated_at=CURRENT_TIMESTAMP
            WHERE workspace_id=? AND session_id=?
            """,
            (
                recent,
                turn_index,
                str(session.workspace.workspace_id),
                str(session.session_id),
            ),
        )
        if cur.rowcount != 1:
            raise RuntimeError("Fast Session context update did not affect exactly one row.")

    def load_turn_messages(self, session: SessionContext, turn_index: int) -> tuple[list, list[str]]:
        """Load one committed Turn for a durable maintenance handler."""
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT message_id, raw FROM messages
                WHERE workspace_id=? AND session_id=? AND turn_index=?
                ORDER BY created_at, message_id
                """,
                (str(session.workspace.workspace_id), str(session.session_id), turn_index),
            ).fetchall()
        raw = [json.loads(row["raw"]) for row in rows]
        return messages_from_dict(raw), [row["message_id"] for row in rows]

    def load_summary_source(
        self,
        session: SessionContext,
        target_turn: int,
    ) -> tuple[str, int, list[tuple[int, object]]]:
        """Load unsummarized committed messages up to a target Turn."""
        with self.database.connect() as conn:
            session_row = conn.execute(
                """
                SELECT summary, summary_through_turn FROM sessions
                WHERE workspace_id=? AND session_id=?
                """,
                (str(session.workspace.workspace_id), str(session.session_id)),
            ).fetchone()
            if not session_row:
                raise RuntimeError("Session disappeared before context summary maintenance.")
            rows = conn.execute(
                """
                SELECT turn_index, raw FROM messages
                WHERE workspace_id=? AND session_id=?
                  AND turn_index > ? AND turn_index <= ?
                ORDER BY turn_index, created_at, message_id
                """,
                (
                    str(session.workspace.workspace_id),
                    str(session.session_id),
                    int(session_row["summary_through_turn"]),
                    target_turn,
                ),
            ).fetchall()
        return (
            session_row["summary"] or "",
            int(session_row["summary_through_turn"]),
            [
                (int(row["turn_index"]), message)
                for row, message in zip(
                    rows,
                    messages_from_dict([json.loads(row["raw"]) for row in rows]),
                    strict=True,
                )
            ],
        )

    def update_summary_cas(
        self,
        session: SessionContext,
        *,
        expected_summary_through_turn: int,
        summary_through_turn: int,
        summary: str,
    ) -> bool:
        """Write a derived summary only when no newer summary won the race."""
        with self.database.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE sessions
                SET summary=?, summary_through_turn=?, version=version + 1,
                    updated_at=CURRENT_TIMESTAMP
                WHERE workspace_id=? AND session_id=? AND summary_through_turn=?
                """,
                (
                    summary,
                    summary_through_turn,
                    str(session.workspace.workspace_id),
                    str(session.session_id),
                    expected_summary_through_turn,
                ),
            )
        return cur.rowcount == 1

    def retrieve_relevant(self, workspace_id: UUID, query: str, limit: int | None = None) -> list[RetrievedMemory]:
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
        selected = [row for row in rows if any(term in row["content"].casefold() for term in terms)]
        if not selected and self._is_memory_recall_query(query):
            # A vague recall request may need a relevance fallback, but it
            # must never inject the entire Workspace memory collection.
            selected = rows[:effective_limit]
        memories = [self._memory_from_row(row) for row in selected[:effective_limit]]
        self._record_retrieval(workspace_id, query, memories, "relevant")
        return memories

    def retrieve_bootstrap(self, workspace_id: UUID, limit: int = MEMORY_BOOTSTRAP_LIMIT) -> list[RetrievedMemory]:
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

    def retrieve_for_turn(self, workspace_id: UUID, query: str, *, new_session: bool) -> list[RetrievedMemory]:
        combined = []
        if new_session:
            combined.extend(self.retrieve_bootstrap(workspace_id))
        combined.extend(self.retrieve_relevant(workspace_id, query))
        unique, seen, chars = [], set(), 0
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
        source_message_ids: list[str],
    ) -> list[str]:
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
        saved, events = [], []
        try:
            with self.database.transaction() as conn:
                for candidate in candidates:
                    content = str(candidate.get("content", "")).strip()
                    importance = int(candidate.get("importance", 3))
                    if not content or importance < self.min_importance or self.extractor.looks_sensitive(content):
                        continue
                    kind = str(candidate.get("kind", "project_fact")).strip() or "project_fact"
                    tags = candidate.get("tags") if isinstance(candidate.get("tags"), list) else []
                    confidence = float(candidate.get("confidence", 0.8))
                    existing = conn.execute(
                        """
                        SELECT memory_id FROM memories
                        WHERE workspace_id = ? AND archived_at IS NULL AND kind = ?
                          AND (content = ? OR substr(content, 1, 160) = ?)
                        LIMIT 1
                        """,
                        (str(session.workspace.workspace_id), kind, content, content[:160]),
                    ).fetchone()
                    memory_id = existing["memory_id"] if existing else str(uuid4())
                    action = "updated" if existing else "created"
                    conn.execute(
                        """
                        INSERT INTO memories(memory_id, workspace_id, kind, content, tags, importance, confidence)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(memory_id) DO UPDATE SET
                            content=excluded.content,
                            tags=excluded.tags,
                            importance=max(memories.importance, excluded.importance),
                            confidence=max(memories.confidence, excluded.confidence),
                            updated_at=CURRENT_TIMESTAMP
                        """,
                        (
                            memory_id,
                            str(session.workspace.workspace_id),
                            kind,
                            content,
                            json.dumps(tags, ensure_ascii=False),
                            importance,
                            confidence,
                        ),
                    )
                    for message_id in source_message_ids:
                        conn.execute(
                            """
                            INSERT INTO memory_sources(workspace_id, memory_id, message_id)
                            VALUES (?, ?, ?)
                            ON CONFLICT DO NOTHING
                            """,
                            (str(session.workspace.workspace_id), memory_id, message_id),
                        )
                    self._enqueue_outbox(conn, "memory_saved", "memory", memory_id, {"action": action})
                    saved.append(f"{action} {memory_id}: {kind}: {content[:120]}")
                    events.append((memory_id, action, kind, importance, content))
        except Exception as exc:
            record_error("agent_memory", "memory_save", exc, "Long-term memory local transaction failed.")
            raise
        for memory_id, action, kind, importance, content in events:
            record_memory_saved(
                "agent_memory",
                memory_id=memory_id,
                action=action,
                kind=kind,
                importance=importance,
                content=content,
                message=f"{action.title()} long-term memory.",
            )
        return saved

    def build_memory_message(self, memories: list[RetrievedMemory]) -> SystemMessage | None:
        if not memories:
            return None
        return SystemMessage(content=f"{MEMORY_MESSAGE_PREFIX}\n{self.format_memories(memories)}")

    def format_memories(self, memories: list[RetrievedMemory]) -> str:
        return "\n".join(
            f"- [{memory.kind} | importance={memory.importance}] {memory.content}" for memory in memories
        ) or "(none)"

    def _append_messages(
        self,
        conn,
        session: SessionContext,
        turn_index: int,
        messages: list,
        *,
        execution_id: str | None = None,
    ) -> list[str]:
        session_row = conn.execute(
            "SELECT active_branch_id FROM sessions WHERE session_id = ?",
            (str(session.session_id),),
        ).fetchone()
        branch_id = session_row["active_branch_id"] if session_row else None
        head = None
        if branch_id:
            branch = conn.execute(
                "SELECT head_message_id FROM branches WHERE branch_id = ?",
                (branch_id,),
            ).fetchone()
            head = branch["head_message_id"] if branch else None
        ids = []
        for message in messages:
            message_id = str(uuid4())
            raw = messages_to_dict([message])[0]
            conn.execute(
                """
                INSERT INTO messages(
                    message_id, workspace_id, session_id, branch_id, parent_message_id,
                    execution_id, role, content, message_type, raw, turn_index
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    str(session.workspace.workspace_id),
                    str(session.session_id),
                    branch_id,
                    head,
                    execution_id,
                    self._message_role(message),
                    self._message_content(message),
                    message.__class__.__name__,
                    json.dumps(raw, ensure_ascii=False, default=str),
                    turn_index,
                ),
            )
            ids.append(message_id)
            head = message_id
        if branch_id and head:
            conn.execute(
                """
                UPDATE branches SET head_message_id = ?, version = version + 1,
                    updated_at = CURRENT_TIMESTAMP WHERE branch_id = ?
                """,
                (head, branch_id),
            )
        return ids

    def _save_session(self, conn, session: SessionContext, state: AgentContextState, turn_index: int) -> None:
        recent = json.dumps(messages_to_dict(state.recent_messages), ensure_ascii=False, default=str)
        cur = conn.execute(
            """
            UPDATE sessions SET summary = ?, recent_messages = ?, turn_index = ?,
                version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE workspace_id = ? AND session_id = ?
            """,
            (
                state.summary,
                recent,
                turn_index,
                str(session.workspace.workspace_id),
                str(session.session_id),
            ),
        )
        if cur.rowcount != 1:
            raise RuntimeError("Session context update did not affect exactly one row.")

    def _enqueue_outbox(self, conn, event_type: str, aggregate_type: str, aggregate_id: str, payload: dict) -> None:
        if not self.projection_enabled:
            return
        conn.execute(
            """
            INSERT INTO projection_outbox(event_type, aggregate_type, aggregate_id, payload)
            VALUES (?, ?, ?, ?)
            """,
            (event_type, aggregate_type, aggregate_id, json.dumps(payload, ensure_ascii=False)),
        )

    def _memory_from_row(self, row) -> RetrievedMemory:
        return RetrievedMemory(
            id=row["memory_id"],
            kind=row["kind"],
            content=row["content"],
            tags=json.loads(row["tags"] or "[]"),
            importance=int(row["importance"]),
            confidence=float(row["confidence"]),
        )

    def _message_role(self, message) -> str:
        role = {
            "HumanMessage": "user",
            "AIMessage": "assistant",
            "ToolMessage": "tool",
            "SystemMessage": "system",
        }.get(message.__class__.__name__)
        if role is None:
            emit_event(
                "unknown_message_role",
                "local_state_store",
                "Archived a message type without a known conversation role.",
                {"message_type": message.__class__.__name__},
                level="warning",
            )
            return "unknown"
        return role

    def _message_content(self, message) -> str:
        content = getattr(message, "content", "")
        return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)

    def _normalize_search_query(self, query: str) -> str:
        return " ".join(re.findall(r"[\w\u4e00-\u9fff]+", query)[:20])

    def _is_memory_recall_query(self, query: str) -> bool:
        lowered = query.casefold()
        return any(term in lowered for term in ("记得", "记忆", "之前", "以前", "remember", "memory"))

    def _record_retrieval(self, workspace_id: UUID, query: str, memories: list[RetrievedMemory], kind: str) -> None:
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
