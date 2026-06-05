CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id TEXT PRIMARY KEY,
    summary TEXT NOT NULL DEFAULT '',
    recent_messages JSONB NOT NULL DEFAULT '[]',
    turn_index INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    message_type TEXT NOT NULL,
    raw JSONB NOT NULL DEFAULT '{}',
    turn_index INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
);

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
);

CREATE INDEX IF NOT EXISTS idx_agent_messages_session
ON agent_messages(session_id, turn_index, id);

CREATE INDEX IF NOT EXISTS idx_agent_memories_scope
ON agent_memories(scope);

CREATE INDEX IF NOT EXISTS idx_agent_memories_kind
ON agent_memories(kind);

CREATE INDEX IF NOT EXISTS idx_agent_memories_importance
ON agent_memories(importance DESC);

CREATE INDEX IF NOT EXISTS idx_agent_memories_content_tsv
ON agent_memories
USING GIN (to_tsvector('simple', content));

CREATE INDEX IF NOT EXISTS idx_agent_events_session_turn
ON agent_events(session_id, turn_index, id);

CREATE INDEX IF NOT EXISTS idx_agent_events_run
ON agent_events(run_id);

CREATE INDEX IF NOT EXISTS idx_agent_events_type
ON agent_events(event_type);
