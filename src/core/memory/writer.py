"""PostgreSQL long-term memory extraction and persistence."""

from uuid import uuid4

from src.config.settings import MEMORY_EXTRACTION_ENABLED
from src.core.telemetry import record_error, record_memory_saved
from src.core.workspace.models import SessionContext


class PostgresMemoryWriter:
    """Extract candidates, upsert memories, and source-link message IDs."""

    def __init__(
        self,
        *,
        memories,
        extractor,
        connect,
        json_param,
        min_importance: int,
    ) -> None:
        self.memories = memories
        self.extractor = extractor
        self._connect = connect
        self._json_param = json_param
        self.min_importance = min_importance

    def extract_and_save(
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
                        if (
                            not content
                            or importance < self.min_importance
                            or self.extractor.looks_sensitive(content)
                        ):
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
