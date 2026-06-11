CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_workspaces (
    workspace_id UUID PRIMARY KEY,
    canonical_path TEXT NOT NULL UNIQUE,
    display_path TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES agent_workspaces(workspace_id),
    session_name TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    recent_messages JSONB NOT NULL DEFAULT '[]',
    turn_index INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, session_name),
    UNIQUE (workspace_id, session_id)
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id BIGSERIAL PRIMARY KEY,
    workspace_id UUID NOT NULL,
    session_id UUID NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    message_type TEXT NOT NULL,
    raw JSONB NOT NULL DEFAULT '{}',
    turn_index INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (workspace_id, session_id)
        REFERENCES agent_sessions(workspace_id, session_id) ON DELETE CASCADE,
    UNIQUE (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS agent_memories (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES agent_workspaces(workspace_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    tags JSONB NOT NULL DEFAULT '[]',
    importance INTEGER NOT NULL DEFAULT 3,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at TIMESTAMPTZ,
    UNIQUE (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS agent_memory_sources (
    workspace_id UUID NOT NULL,
    memory_id UUID NOT NULL,
    message_id BIGINT NOT NULL,
    FOREIGN KEY (workspace_id, memory_id)
        REFERENCES agent_memories(workspace_id, id) ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, message_id)
        REFERENCES agent_messages(workspace_id, id) ON DELETE CASCADE,
    PRIMARY KEY (memory_id, message_id)
);

CREATE TABLE IF NOT EXISTS agent_events (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    workspace_id UUID,
    session_id UUID,
    turn_index INTEGER,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}',
    duration_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((workspace_id IS NULL) = (session_id IS NULL)),
    FOREIGN KEY (workspace_id, session_id)
        REFERENCES agent_sessions(workspace_id, session_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_agent_sessions_workspace
ON agent_sessions(workspace_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_messages_session
ON agent_messages(workspace_id, session_id, turn_index, id);

CREATE INDEX IF NOT EXISTS idx_agent_memories_workspace
ON agent_memories(workspace_id, importance DESC, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_memories_kind
ON agent_memories(workspace_id, kind);

CREATE INDEX IF NOT EXISTS idx_agent_memories_content_tsv
ON agent_memories USING GIN (to_tsvector('simple', content));

CREATE INDEX IF NOT EXISTS idx_agent_events_session_turn
ON agent_events(workspace_id, session_id, turn_index, id);

CREATE INDEX IF NOT EXISTS idx_agent_events_run
ON agent_events(run_id);

CREATE INDEX IF NOT EXISTS idx_agent_events_type
ON agent_events(event_type);
