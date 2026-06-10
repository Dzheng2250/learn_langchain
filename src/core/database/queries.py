from pathlib import Path


SQL_DIR = Path(__file__).resolve().parent / "sql"


def load_sql_file(filename: str) -> str:
    """Load a managed SQL file from the local sql directory."""
    path = (SQL_DIR / filename).resolve()
    if SQL_DIR not in path.parents and path != SQL_DIR:
        raise ValueError(f"SQL file must be inside {SQL_DIR}: {filename}")
    return path.read_text(encoding="utf-8")


def execute_sql_file(cur, filename: str) -> None:
    """Execute semicolon-separated SQL statements from a managed SQL file."""
    for statement in split_sql_statements(load_sql_file(filename)):
        cur.execute(statement)


def split_sql_statements(sql: str) -> list[str]:
    """Split simple migration/schema SQL into executable statements."""
    statements = []
    for part in sql.split(";"):
        statement = part.strip()
        if statement:
            statements.append(statement)
    return statements


# Keep values out of SQL strings. All runtime data must be passed through
# psycopg parameters, never formatted into these constants.

SELECT_SESSION_CONTEXT = """
SELECT summary, recent_messages, turn_index
FROM agent_sessions
WHERE session_id = %s
"""

UPSERT_SESSION_CONTEXT = """
INSERT INTO agent_sessions (
    session_id, summary, recent_messages, turn_index, updated_at
)
VALUES (%s, %s, %s, %s, now())
ON CONFLICT (session_id) DO UPDATE SET
    summary = EXCLUDED.summary,
    recent_messages = EXCLUDED.recent_messages,
    turn_index = EXCLUDED.turn_index,
    updated_at = now()
"""

INSERT_AGENT_MESSAGE = """
INSERT INTO agent_messages (
    session_id, role, content, message_type, raw, turn_index
)
VALUES (%s, %s, %s, %s, %s, %s)
RETURNING id
"""

SELECT_RELEVANT_MEMORIES = """
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
"""

SELECT_RECENT_IMPORTANT_MEMORIES = """
SELECT id::text, scope, kind, content, tags, importance, confidence
FROM agent_memories
WHERE archived_at IS NULL
  AND scope IN (%s, 'global')
ORDER BY importance DESC, updated_at DESC
LIMIT %s
"""

UPDATE_AGENT_MEMORY = """
UPDATE agent_memories
SET content = %s,
    tags = %s,
    importance = GREATEST(importance, %s),
    confidence = GREATEST(confidence, %s),
    source_message_ids = %s,
    updated_at = now()
WHERE id = %s
"""

INSERT_AGENT_MEMORY = """
INSERT INTO agent_memories (
    id, scope, kind, content, tags, importance,
    confidence, source_message_ids
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""

SELECT_SIMILAR_MEMORY_ID = """
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
"""

INSERT_AGENT_EVENT = """
INSERT INTO agent_events (
    run_id, session_id, turn_index, event_type, source,
    level, message, payload, duration_ms, created_at
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
