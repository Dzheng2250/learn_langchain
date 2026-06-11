"""Focused PostgreSQL repositories used by the memory facade."""

from uuid import UUID

from src.core.database.queries import (
    INSERT_AGENT_MEMORY,
    INSERT_AGENT_MESSAGE,
    INSERT_MEMORY_SOURCE,
    SELECT_RECENT_IMPORTANT_MEMORIES,
    SELECT_RELEVANT_MEMORIES,
    SELECT_SESSION_CONTEXT,
    SELECT_SIMILAR_MEMORY_ID,
    UPDATE_AGENT_MEMORY,
    UPDATE_SESSION_CONTEXT,
)
from src.core.workspace.models import SessionContext


class SessionRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    def load(self, session: SessionContext):
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(SELECT_SESSION_CONTEXT, (session.workspace.workspace_id, session.session_id))
                return cur.fetchone()

    def update(self, session: SessionContext, summary: str, recent_messages, turn_index: int) -> None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    UPDATE_SESSION_CONTEXT,
                    (
                        summary,
                        recent_messages,
                        turn_index,
                        session.workspace.workspace_id,
                        session.session_id,
                    ),
                )
                if cur.rowcount != 1:
                    raise RuntimeError("Session context update did not affect exactly one row.")
            conn.commit()


class MessageRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    def append(self, session: SessionContext, turn_index: int, rows: list[tuple]) -> list[int]:
        ids = []
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                for role, content, message_type, raw in rows:
                    cur.execute(
                        INSERT_AGENT_MESSAGE,
                        (
                            session.workspace.workspace_id,
                            session.session_id,
                            role,
                            content,
                            message_type,
                            raw,
                            turn_index,
                        ),
                    )
                    ids.append(cur.fetchone()[0])
            conn.commit()
        return ids


class MemoryRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    def relevant(self, workspace_id: UUID, normalized: str, raw_query: str, limit: int):
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    SELECT_RELEVANT_MEMORIES,
                    (workspace_id, normalized, f"%{raw_query[:120]}%", normalized, limit),
                )
                return cur.fetchall()

    def recent_important(self, workspace_id: UUID, limit: int):
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(SELECT_RECENT_IMPORTANT_MEMORIES, (workspace_id, limit))
                return cur.fetchall()

    def find_similar(self, cur, workspace_id: UUID, kind: str, content: str) -> str | None:
        cur.execute(SELECT_SIMILAR_MEMORY_ID, (workspace_id, kind, content, content[:160]))
        row = cur.fetchone()
        return row[0] if row else None

    def update(self, cur, workspace_id: UUID, memory_id: str, content: str, tags, importance: int, confidence: float):
        cur.execute(
            UPDATE_AGENT_MEMORY,
            (content, tags, importance, confidence, workspace_id, memory_id),
        )

    def insert(self, cur, memory_id: str, workspace_id: UUID, kind: str, content: str, tags, importance: int, confidence: float):
        cur.execute(
            INSERT_AGENT_MEMORY,
            (memory_id, workspace_id, kind, content, tags, importance, confidence),
        )

    def add_sources(self, cur, workspace_id: UUID, memory_id: str, message_ids: list[int]) -> None:
        for message_id in message_ids:
            cur.execute(INSERT_MEMORY_SOURCE, (workspace_id, memory_id, message_id))
