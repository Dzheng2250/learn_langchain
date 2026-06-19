"""SQLite long-term memory write adapter."""

from __future__ import annotations

import json
from uuid import uuid4

from src.config.settings import MEMORY_EXTRACTION_ENABLED
from src.core.memory.extractor import MemoryCandidateExtractor
from src.core.telemetry import record_error, record_memory_saved


class SQLiteMemoryWriteStore:
    """Extract and persist long-term memories into the local state database."""

    def __init__(
        self,
        database,
        *,
        extractor: MemoryCandidateExtractor,
        min_importance: int,
        projection_enabled: bool,
    ) -> None:
        self.database = database
        self.extractor = extractor
        self.min_importance = min_importance
        self.projection_enabled = projection_enabled

    def extract_and_save(
        self,
        session,
        turn_index: int,
        messages: list,
        source_message_ids: list[str],
    ) -> list[str]:
        """Extract memory candidates and save accepted ones transactionally."""
        if not MEMORY_EXTRACTION_ENABLED:
            return []
        source = self.extractor.format_messages(messages)
        if not source.strip():
            return []
        try:
            candidates = self.extractor.extract(source)
        except Exception as exc:
            record_error(
                "agent_memory",
                "memory",
                exc,
                "Long-term memory extraction failed.",
            )
            raise
        saved, events = [], []
        try:
            with self.database.transaction() as conn:
                for candidate in candidates:
                    accepted = self._normalize_candidate(candidate)
                    if accepted is None:
                        continue
                    kind, content, tags, importance, confidence = accepted
                    existing = conn.execute(
                        """
                        SELECT memory_id FROM memories
                        WHERE workspace_id = ? AND archived_at IS NULL AND kind = ?
                          AND (content = ? OR substr(content, 1, 160) = ?)
                        LIMIT 1
                        """,
                        (
                            str(session.workspace.workspace_id),
                            kind,
                            content,
                            content[:160],
                        ),
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
                            (
                                str(session.workspace.workspace_id),
                                memory_id,
                                message_id,
                            ),
                        )
                    self._enqueue_outbox(
                        conn,
                        "memory_saved",
                        "memory",
                        memory_id,
                        {"action": action},
                    )
                    saved.append(f"{action} {memory_id}: {kind}: {content[:120]}")
                    events.append((memory_id, action, kind, importance, content))
        except Exception as exc:
            record_error(
                "agent_memory",
                "memory_save",
                exc,
                "Long-term memory local transaction failed.",
            )
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

    def _normalize_candidate(self, candidate: dict):
        content = str(candidate.get("content", "")).strip()
        importance = int(candidate.get("importance", 3))
        if (
            not content
            or importance < self.min_importance
            or self.extractor.looks_sensitive(content)
        ):
            return None
        kind = str(candidate.get("kind", "project_fact")).strip() or "project_fact"
        tags = candidate.get("tags") if isinstance(candidate.get("tags"), list) else []
        confidence = float(candidate.get("confidence", 0.8))
        return kind, content, tags, importance, confidence

    def _enqueue_outbox(
        self,
        conn,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict,
    ) -> None:
        if not self.projection_enabled:
            return
        conn.execute(
            """
            INSERT INTO projection_outbox(event_type, aggregate_type, aggregate_id, payload)
            VALUES (?, ?, ?, ?)
            """,
            (
                event_type,
                aggregate_type,
                aggregate_id,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
