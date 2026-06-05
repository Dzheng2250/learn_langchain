UPSERT_TEST_MEMORY = """
INSERT INTO agent_memories (
    id, scope, kind, content, tags, importance,
    confidence, source_message_ids, archived_at
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL)
ON CONFLICT (id) DO UPDATE SET
    scope = EXCLUDED.scope,
    kind = EXCLUDED.kind,
    content = EXCLUDED.content,
    tags = EXCLUDED.tags,
    importance = EXCLUDED.importance,
    confidence = EXCLUDED.confidence,
    source_message_ids = EXCLUDED.source_message_ids,
    archived_at = NULL,
    updated_at = now()
"""

SELECT_TEST_ARCHIVED_MESSAGES = """
SELECT role, message_type, content
FROM agent_messages
WHERE session_id = %s
ORDER BY id
"""

DELETE_TEST_MESSAGES = "DELETE FROM agent_messages WHERE session_id = %s"

DELETE_TEST_SESSION = "DELETE FROM agent_sessions WHERE session_id = %s"

DELETE_TEST_MEMORIES = "DELETE FROM agent_memories WHERE id = %s OR scope = %s OR content ILIKE %s"

SELECT_TEST_EVENT = """
SELECT event_type, source, message, payload
FROM agent_events
WHERE run_id = %s
"""

DELETE_TEST_EVENTS = "DELETE FROM agent_events WHERE run_id = %s"
