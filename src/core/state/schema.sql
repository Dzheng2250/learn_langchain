PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS local_schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    canonical_path TEXT NOT NULL UNIQUE,
    display_path TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    session_name TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    recent_messages TEXT NOT NULL DEFAULT '[]',
    turn_index INTEGER NOT NULL DEFAULT 0,
    summary_through_turn INTEGER NOT NULL DEFAULT 0,
    active_context_window_id TEXT,
    version INTEGER NOT NULL DEFAULT 0,
    active_branch_id TEXT,
    pending_execution_id TEXT,
    tool_approval_mode TEXT,
    archived_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(workspace_id, session_name),
    -- Required as the parent key for composite Workspace-isolated foreign
    -- keys. session_id is globally unique, but that alone cannot validate
    -- that child rows carry the matching workspace_id.
    UNIQUE(workspace_id, session_id)
);

CREATE TABLE IF NOT EXISTS branches (
    branch_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    head_message_id TEXT,
    created_from_message_id TEXT,
    version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(workspace_id, session_id)
        REFERENCES sessions(workspace_id, session_id) ON DELETE CASCADE,
    UNIQUE(session_id, branch_name)
);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    legacy_message_id INTEGER,
    workspace_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    branch_id TEXT,
    parent_message_id TEXT,
    execution_id TEXT,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    message_type TEXT NOT NULL,
    raw TEXT NOT NULL DEFAULT '{}',
    artifact_id TEXT,
    turn_index INTEGER NOT NULL,
    message_ordinal INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(workspace_id, session_id)
        REFERENCES sessions(workspace_id, session_id) ON DELETE CASCADE,
    FOREIGN KEY(branch_id) REFERENCES branches(branch_id) ON DELETE SET NULL,
    FOREIGN KEY(parent_message_id) REFERENCES messages(message_id) ON DELETE SET NULL
);


-- v1 models a single linear compression lineage per Session. branch_id is
-- reserved for future branch-local windows; supporting multiple active branches
-- will require moving active_context_window_id from sessions to a branch-level
-- mapping or to the branches table.
CREATE TABLE IF NOT EXISTS context_windows (
    window_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    branch_id TEXT,
    first_window_id TEXT NOT NULL,
    previous_window_id TEXT,
    summary_text TEXT NOT NULL DEFAULT '',
    summary_through_turn INTEGER NOT NULL DEFAULT 0,
    compacted_from_turn INTEGER NOT NULL DEFAULT 0,
    compacted_through_turn INTEGER NOT NULL DEFAULT 0,
    opened_at_turn INTEGER NOT NULL DEFAULT 0,
    closed_at_turn INTEGER,
    source_message_count INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    model TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(workspace_id, session_id)
        REFERENCES sessions(workspace_id, session_id) ON DELETE CASCADE,
    FOREIGN KEY(branch_id) REFERENCES branches(branch_id) ON DELETE SET NULL,
    FOREIGN KEY(previous_window_id) REFERENCES context_windows(window_id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    checkpoint_thread_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (
        status IN (
            'running', 'paused_budget', 'paused_error', 'paused_confirmation',
            'paused_recovery', 'unrecoverable_checkpoint', 'completed', 'discarded'
        )
    ),
    stop_reason TEXT NOT NULL DEFAULT '',
    original_input TEXT NOT NULL,
    progress_summary TEXT NOT NULL DEFAULT '',
    grant_index INTEGER NOT NULL DEFAULT 1,
    slice_index INTEGER NOT NULL DEFAULT 0,
    graph_steps_used INTEGER NOT NULL DEFAULT 0,
    controlled_executions_used INTEGER NOT NULL DEFAULT 0,
    delegations_used INTEGER NOT NULL DEFAULT 0,
    tool_calls_used INTEGER NOT NULL DEFAULT 0,
    goal_mode INTEGER NOT NULL DEFAULT 0,
    checkpoint_state TEXT NOT NULL DEFAULT 'uninitialized' CHECK (
        checkpoint_state IN ('uninitialized', 'available', 'cleanup_pending', 'cleaned', 'missing')
    ),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    FOREIGN KEY(workspace_id, session_id)
        REFERENCES sessions(workspace_id, session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS execution_slices (
    slice_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES executions(execution_id) ON DELETE CASCADE,
    grant_index INTEGER NOT NULL,
    slice_index INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'running', 'paused_budget', 'paused_error', 'paused_confirmation',
            'paused_recovery', 'completed', 'discarded'
        )
    ),
    stop_reason TEXT NOT NULL DEFAULT '',
    graph_steps_used INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    UNIQUE(execution_id, grant_index, slice_index)
);

CREATE TABLE IF NOT EXISTS execution_tasks (
    task_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES executions(execution_id) ON DELETE CASCADE,
    task_key TEXT NOT NULL,
    subject TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'in_progress', 'completed', 'cancelled')
    ),
    notes TEXT NOT NULL DEFAULT '',
    ordinal INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    UNIQUE(execution_id, task_key),
    -- Required by SQLite composite foreign keys from execution_task_dependencies.
    -- task_id is globally unique, but the child table also carries execution_id
    -- to prevent cross-Execution dependency links at the schema boundary.
    UNIQUE(execution_id, task_id)
);

CREATE TABLE IF NOT EXISTS execution_task_dependencies (
    execution_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    depends_on_task_id TEXT NOT NULL,
    PRIMARY KEY(execution_id, task_id, depends_on_task_id),
    FOREIGN KEY(execution_id, task_id)
        REFERENCES execution_tasks(execution_id, task_id) ON DELETE CASCADE,
    FOREIGN KEY(execution_id, depends_on_task_id)
        REFERENCES execution_tasks(execution_id, task_id) ON DELETE CASCADE,
    CHECK(task_id <> depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS tool_ledger (
    tool_call_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES executions(execution_id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    risk TEXT NOT NULL,
    status TEXT NOT NULL,
    args_preview TEXT NOT NULL DEFAULT '',
    result_preview TEXT NOT NULL DEFAULT '',
    artifact_id TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS tool_approval_requests (
    request_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    execution_id TEXT NOT NULL REFERENCES executions(execution_id) ON DELETE CASCADE,
    tool_call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    actor TEXT NOT NULL,
    args_summary TEXT NOT NULL DEFAULT '{}',
    capabilities TEXT NOT NULL DEFAULT '[]',
    rule_key TEXT NOT NULL DEFAULT '',
    persistable INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    approval_mode TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'resolved')),
    response TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT,
    UNIQUE(execution_id, tool_call_id)
);

CREATE TABLE IF NOT EXISTS tool_permission_rules (
    rule_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    session_id TEXT,
    tool_name TEXT NOT NULL,
    rule_key TEXT NOT NULL,
    effect TEXT NOT NULL CHECK(effect IN ('allow', 'deny')),
    created_from_request_id TEXT REFERENCES tool_approval_requests(request_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(workspace_id, session_id, tool_name, rule_key),
    FOREIGN KEY(workspace_id, session_id)
        REFERENCES sessions(workspace_id, session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tool_approval_audit (
    audit_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES tool_approval_requests(request_id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    response TEXT NOT NULL,
    decision_source TEXT NOT NULL DEFAULT 'legacy',
    approval_mode TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE IF NOT EXISTS resource_activity_counters (
    execution_id TEXT PRIMARY KEY REFERENCES executions(execution_id) ON DELETE CASCADE,
    recorded_count INTEGER NOT NULL DEFAULT 0,
    dropped_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS resource_activities (
    activity_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL,
    workspace_id TEXT NOT NULL, session_id TEXT NOT NULL, turn_index INTEGER,
    execution_id TEXT NOT NULL REFERENCES executions(execution_id) ON DELETE CASCADE,
    slice_id TEXT, run_id TEXT NOT NULL DEFAULT '', tool_call_id TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL DEFAULT 'parent',
    resource_uri TEXT NOT NULL, operation TEXT NOT NULL, observation_mode TEXT NOT NULL,
    change_state TEXT NOT NULL, requested_range TEXT, observed_range TEXT,
    returned_bytes INTEGER NOT NULL DEFAULT 0, resource_bytes INTEGER NOT NULL DEFAULT 0,
    before_digest TEXT NOT NULL DEFAULT '', after_digest TEXT NOT NULL DEFAULT '',
    before_lines INTEGER, after_lines INTEGER,
    evidence_status TEXT NOT NULL DEFAULT 'not_applicable',
    related_activity_ids TEXT NOT NULL DEFAULT '[]', metadata TEXT NOT NULL DEFAULT '{}',
    event_key TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(execution_id, sequence),
    FOREIGN KEY(workspace_id, session_id) REFERENCES sessions(workspace_id, session_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    importance INTEGER NOT NULL DEFAULT 3,
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at TEXT,
    UNIQUE(workspace_id, memory_id)
);

CREATE TABLE IF NOT EXISTS memory_sources (
    workspace_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    FOREIGN KEY(workspace_id, memory_id)
        REFERENCES memories(workspace_id, memory_id) ON DELETE CASCADE,
    FOREIGN KEY(message_id) REFERENCES messages(message_id) ON DELETE CASCADE,
    PRIMARY KEY(memory_id, message_id)
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    content_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    relative_path TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS artifact_references (
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE CASCADE,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(artifact_id, owner_type, owner_id)
);

CREATE TABLE IF NOT EXISTS projection_outbox (
    outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    projected_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS maintenance_jobs (
    job_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    execution_id TEXT,
    job_type TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    priority INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'running', 'succeeded', 'failed')
    ),
    payload TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    next_attempt_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_expires_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    FOREIGN KEY(workspace_id, session_id)
        REFERENCES sessions(workspace_id, session_id) ON DELETE CASCADE,
    FOREIGN KEY(execution_id) REFERENCES executions(execution_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS imported_events (
    source_event_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    workspace_id TEXT,
    session_id TEXT,
    turn_index INTEGER,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    payload TEXT NOT NULL,
    duration_ms INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_workspace
ON sessions(workspace_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_messages_session
ON messages(workspace_id, session_id, turn_index, message_ordinal);

CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_session_ordinal_unique
ON messages(workspace_id, session_id, message_ordinal);

CREATE INDEX IF NOT EXISTS idx_context_windows_session
ON context_windows(workspace_id, session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_executions_session
ON executions(workspace_id, session_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_execution_tasks_execution
ON execution_tasks(execution_id, ordinal, task_key);

CREATE INDEX IF NOT EXISTS idx_tool_approval_pending
ON tool_approval_requests(status, workspace_id, session_id, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_approval_audit_request
ON tool_approval_audit(request_id);

CREATE INDEX IF NOT EXISTS idx_tool_permission_rules_lookup
ON tool_permission_rules(workspace_id, tool_name, rule_key, session_id);

CREATE INDEX IF NOT EXISTS idx_execution_task_dependencies_dep
ON execution_task_dependencies(execution_id, depends_on_task_id);


CREATE INDEX IF NOT EXISTS idx_resource_activities_execution ON resource_activities(execution_id, sequence);
CREATE INDEX IF NOT EXISTS idx_resource_activities_session_turn ON resource_activities(workspace_id, session_id, turn_index, sequence);
CREATE INDEX IF NOT EXISTS idx_resource_activities_run ON resource_activities(run_id, sequence);
CREATE INDEX IF NOT EXISTS idx_resource_activities_tool_call ON resource_activities(tool_call_id, sequence);
CREATE INDEX IF NOT EXISTS idx_resource_activities_uri ON resource_activities(execution_id, resource_uri, sequence);
CREATE UNIQUE INDEX IF NOT EXISTS idx_resource_activities_event_key
ON resource_activities(execution_id, event_key) WHERE event_key <> '';
CREATE INDEX IF NOT EXISTS idx_memories_workspace
ON memories(workspace_id, importance DESC, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_outbox_pending
ON projection_outbox(projected_at, outbox_id);

CREATE INDEX IF NOT EXISTS idx_maintenance_jobs_ready
ON maintenance_jobs(status, next_attempt_at, priority DESC, created_at);

CREATE INDEX IF NOT EXISTS idx_maintenance_jobs_session
ON maintenance_jobs(workspace_id, session_id, status, created_at);
