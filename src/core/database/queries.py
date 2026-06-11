"""Managed parameterized SQL used by Core repositories."""

from pathlib import Path


SQL_DIR = Path(__file__).resolve().parent / "sql"


def load_sql_file(filename: str) -> str:
    path = (SQL_DIR / filename).resolve()
    if SQL_DIR not in path.parents and path != SQL_DIR:
        raise ValueError(f"SQL file must be inside {SQL_DIR}: {filename}")
    return path.read_text(encoding="utf-8")


def execute_sql_file(cur, filename: str) -> None:
    for statement in split_sql_statements(load_sql_file(filename)):
        cur.execute(statement)


def split_sql_statements(sql: str) -> list[str]:
    return [part.strip() for part in sql.split(";") if part.strip()]


DETECT_LEGACY_SCHEMA = """
SELECT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'agent_sessions'
      AND column_name = 'workspace_id'
) AS is_current,
EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'agent_sessions'
) AS has_sessions
"""

SELECT_SCHEMA_VERSION = "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
HAS_SCHEMA_MIGRATIONS = "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
INSERT_SCHEMA_MIGRATION = """
INSERT INTO schema_migrations(version, name)
VALUES (%s, %s)
ON CONFLICT (version) DO NOTHING
"""

UPSERT_WORKSPACE = """
INSERT INTO agent_workspaces(workspace_id, canonical_path, display_path)
VALUES (%s, %s, %s)
ON CONFLICT (canonical_path) DO UPDATE SET
    display_path = EXCLUDED.display_path,
    updated_at = now()
RETURNING workspace_id
"""

SELECT_OR_CREATE_SESSION = """
INSERT INTO agent_sessions(session_id, workspace_id, session_name)
VALUES (%s, %s, %s)
ON CONFLICT (workspace_id, session_name) DO UPDATE SET
    updated_at = agent_sessions.updated_at
RETURNING session_id, summary, recent_messages, turn_index,
          (agent_sessions.created_at = agent_sessions.updated_at) AS is_new
"""

SELECT_SESSION_CONTEXT = """
SELECT summary, recent_messages, turn_index
FROM agent_sessions
WHERE workspace_id = %s AND session_id = %s
"""

UPDATE_SESSION_CONTEXT = """
UPDATE agent_sessions
SET summary = %s, recent_messages = %s, turn_index = %s, updated_at = now()
WHERE workspace_id = %s AND session_id = %s
"""

INSERT_AGENT_MESSAGE = """
INSERT INTO agent_messages(
    workspace_id, session_id, role, content, message_type, raw, turn_index
)
VALUES (%s, %s, %s, %s, %s, %s, %s)
RETURNING id
"""

SELECT_RELEVANT_MEMORIES = """
SELECT id::text, kind, content, tags, importance, confidence
FROM agent_memories
WHERE archived_at IS NULL
  AND workspace_id = %s
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
SELECT id::text, kind, content, tags, importance, confidence
FROM agent_memories
WHERE archived_at IS NULL AND workspace_id = %s
ORDER BY importance DESC, updated_at DESC
LIMIT %s
"""

UPDATE_AGENT_MEMORY = """
UPDATE agent_memories
SET content = %s,
    tags = %s,
    importance = GREATEST(importance, %s),
    confidence = GREATEST(confidence, %s),
    updated_at = now()
WHERE workspace_id = %s AND id = %s
"""

INSERT_AGENT_MEMORY = """
INSERT INTO agent_memories(
    id, workspace_id, kind, content, tags, importance, confidence
)
VALUES (%s, %s, %s, %s, %s, %s, %s)
"""

INSERT_MEMORY_SOURCE = """
INSERT INTO agent_memory_sources(workspace_id, memory_id, message_id)
VALUES (%s, %s, %s)
ON CONFLICT DO NOTHING
"""

SELECT_SIMILAR_MEMORY_ID = """
SELECT id::text
FROM agent_memories
WHERE archived_at IS NULL
  AND workspace_id = %s
  AND kind = %s
  AND (content = %s OR left(content, 160) = %s)
LIMIT 1
"""

INSERT_AGENT_EVENT = """
INSERT INTO agent_events(
    run_id, workspace_id, session_id, turn_index, event_type, source,
    level, message, payload, duration_ms, created_at
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
