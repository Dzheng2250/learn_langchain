"""Idempotent transactional migrations for the authoritative local state database."""


LATEST_SCHEMA_VERSION = 11


def apply_local_migrations(conn) -> None:
    """Upgrade an existing local database without deleting user state."""
    current_version = int(
        conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM local_schema_migrations"
        ).fetchone()[0]
    )
    if current_version > LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            "Local state schema is newer than this Core version supports "
            f"({current_version} > {LATEST_SCHEMA_VERSION})."
        )
    applied_versions = {
        int(row["version"])
        for row in conn.execute("SELECT version FROM local_schema_migrations")
    }
    if current_version < 2 or 2 not in applied_versions:
        _ensure_column(
            conn,
            "sessions",
            "summary_through_turn",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(
            conn,
            "executions",
            "checkpoint_state",
            "TEXT NOT NULL DEFAULT 'uninitialized'",
        )
        _ensure_column(conn, "executions", "completed_at", "TEXT")
        _record_migration(conn, 2, "durable_maintenance_and_checkpoint_state")
    if current_version < 3 or 3 not in applied_versions:
        _ensure_state_validation_triggers(conn)
        _record_migration(conn, 3, "typed_domain_state_validation")
    if current_version < 4 or 4 not in applied_versions:
        _ensure_column(
            conn,
            "executions",
            "goal_mode",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_execution_task_tables(conn)
        _create_validation_triggers(
            conn,
            "execution_tasks",
            "status",
            ("pending", "in_progress", "completed", "cancelled"),
        )
        _record_migration(conn, 4, "execution_private_tasks")
    if current_version < 5 or 5 not in applied_versions:
        _ensure_column(
            conn,
            "sessions",
            "context_tokens",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _record_migration(conn, 5, "session_context_tokens")
    if current_version < 6 or 6 not in applied_versions:
        _ensure_column(conn, "sessions", "archived_at", "TEXT")
        _record_migration(conn, 6, "session_archive_marker")
    if current_version < 7 or 7 not in applied_versions:
        _ensure_context_window_lineage(conn)
        _record_migration(conn, 7, "context_window_lineage")
    if current_version < 8 or 8 not in applied_versions:
        _ensure_tool_approval_tables(conn)
        _record_migration(conn, 8, "tool_approval_policy")
    if current_version < 9 or 9 not in applied_versions:
        _ensure_tool_permission_session_foreign_key(conn)
        _record_migration(conn, 9, "tool_permission_session_integrity")
    if current_version < 10 or 10 not in applied_versions:
        _ensure_tool_approval_audit_request_index(conn)
        _record_migration(conn, 10, "tool_approval_audit_request_index")
    if current_version < 11 or 11 not in applied_versions:
        _ensure_resource_activity_tables(conn)
        _record_migration(conn, 11, "resource_activity_ledger")
    else:
        _ensure_resource_activity_tables(conn)


def _ensure_resource_activity_tables(conn) -> None:
    """Add the append-only frontend-neutral resource activity ledger."""
    statements = (
        """
        CREATE TABLE IF NOT EXISTS resource_activity_counters (
            execution_id TEXT PRIMARY KEY REFERENCES executions(execution_id) ON DELETE CASCADE,
            recorded_count INTEGER NOT NULL DEFAULT 0,
            dropped_count INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
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
            FOREIGN KEY(workspace_id, session_id)
                REFERENCES sessions(workspace_id, session_id) ON DELETE CASCADE
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_resource_activities_execution ON resource_activities(execution_id, sequence)",
        "CREATE INDEX IF NOT EXISTS idx_resource_activities_session_turn ON resource_activities(workspace_id, session_id, turn_index, sequence)",
        "CREATE INDEX IF NOT EXISTS idx_resource_activities_run ON resource_activities(run_id, sequence)",
        "CREATE INDEX IF NOT EXISTS idx_resource_activities_tool_call ON resource_activities(tool_call_id, sequence)",
        "CREATE INDEX IF NOT EXISTS idx_resource_activities_uri ON resource_activities(execution_id, resource_uri, sequence)",
    )
    for statement in statements:
        conn.execute(statement)
    _ensure_column(conn, "resource_activities", "event_key", "TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_resource_activities_event_key ON resource_activities(execution_id, event_key) WHERE event_key <> ''")

SUPPORTED_LOCAL_SCHEMA_DOWNGRADES = frozenset({(11, 10)})


def validate_local_schema_downgrade(*, from_version: int, to_version: int) -> None:
    """Reject unsupported downgrade transitions before creating a backup."""
    transition = (int(from_version), int(to_version))
    if transition not in SUPPORTED_LOCAL_SCHEMA_DOWNGRADES:
        raise ValueError(
            f"Unsupported local schema downgrade: v{from_version} -> v{to_version}."
        )


def downgrade_local_schema(conn, *, from_version: int, to_version: int) -> None:
    """Run one explicitly supported offline downgrade transition."""
    validate_local_schema_downgrade(from_version=from_version, to_version=to_version)
    current = int(
        conn.execute("SELECT COALESCE(MAX(version), 0) FROM local_schema_migrations").fetchone()[0]
    )
    if current != int(from_version):
        raise ValueError(f"Expected local schema v{from_version}, found v{current}.")
    downgrade_v11_to_v10(conn)

def downgrade_v11_to_v10(conn) -> None:
    """Remove v11 observations; rollback intentionally discards only derived metadata."""
    conn.execute("DROP TABLE IF EXISTS resource_activities")
    conn.execute("DROP TABLE IF EXISTS resource_activity_counters")
    conn.execute("DELETE FROM local_schema_migrations WHERE version=11")

def _ensure_tool_approval_tables(conn) -> None:
    """Add durable approval requests, scoped rules, and audit records."""
    statements = (
        """
        CREATE TABLE IF NOT EXISTS tool_approval_requests (
            request_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
            session_id TEXT NOT NULL, execution_id TEXT NOT NULL,
            tool_call_id TEXT NOT NULL, tool_name TEXT NOT NULL,
            actor TEXT NOT NULL, args_summary TEXT NOT NULL DEFAULT '{}',
            capabilities TEXT NOT NULL DEFAULT '[]', rule_key TEXT NOT NULL DEFAULT '',
            persistable INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending', response TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, resolved_at TEXT,
            UNIQUE(execution_id, tool_call_id),
            FOREIGN KEY(execution_id) REFERENCES executions(execution_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS tool_permission_rules (
            rule_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
            session_id TEXT, tool_name TEXT NOT NULL, rule_key TEXT NOT NULL,
            effect TEXT NOT NULL, created_from_request_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(workspace_id, session_id, tool_name, rule_key),
            FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
            FOREIGN KEY(workspace_id, session_id)
                REFERENCES sessions(workspace_id, session_id) ON DELETE CASCADE,
            FOREIGN KEY(created_from_request_id) REFERENCES tool_approval_requests(request_id) ON DELETE SET NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS tool_approval_audit (
            audit_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL, session_id TEXT NOT NULL,
            execution_id TEXT NOT NULL, tool_call_id TEXT NOT NULL,
            tool_name TEXT NOT NULL, response TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(request_id) REFERENCES tool_approval_requests(request_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_tool_approval_pending
        ON tool_approval_requests(status, workspace_id, session_id, created_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_tool_permission_rules_lookup
        ON tool_permission_rules(workspace_id, tool_name, rule_key, session_id)
        """,
    )
    for statement in statements:
        conn.execute(statement)


def _ensure_tool_permission_session_foreign_key(conn) -> None:
    """Rebuild v8 permission rules when the Session composite FK is absent."""
    if not _table_exists(conn, "tool_permission_rules"):
        return
    foreign_keys = conn.execute(
        "PRAGMA foreign_key_list(tool_permission_rules)"
    ).fetchall()
    grouped: dict[int, set[tuple[str, str]]] = {}
    for row in foreign_keys:
        if row["table"] == "sessions":
            grouped.setdefault(int(row["id"]), set()).add(
                (str(row["from"]), str(row["to"]))
            )
    expected = {("workspace_id", "workspace_id"), ("session_id", "session_id")}
    if expected in grouped.values():
        return
    conn.execute(
        """
        CREATE TABLE tool_permission_rules_v9 (
            rule_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
            session_id TEXT, tool_name TEXT NOT NULL, rule_key TEXT NOT NULL,
            effect TEXT NOT NULL CHECK(effect IN ('allow', 'deny')),
            created_from_request_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(workspace_id, session_id, tool_name, rule_key),
            FOREIGN KEY(workspace_id) REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
            FOREIGN KEY(workspace_id, session_id)
                REFERENCES sessions(workspace_id, session_id) ON DELETE CASCADE,
            FOREIGN KEY(created_from_request_id)
                REFERENCES tool_approval_requests(request_id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO tool_permission_rules_v9(
            rule_id, workspace_id, session_id, tool_name, rule_key, effect,
            created_from_request_id, created_at, updated_at
        )
        SELECT r.rule_id, r.workspace_id, r.session_id, r.tool_name, r.rule_key,
               r.effect, r.created_from_request_id, r.created_at, r.updated_at
        FROM tool_permission_rules r
        WHERE r.session_id IS NULL OR EXISTS (
            SELECT 1 FROM sessions s
            WHERE s.workspace_id=r.workspace_id AND s.session_id=r.session_id
        )
        """
    )
    conn.execute("DROP TABLE tool_permission_rules")
    conn.execute("ALTER TABLE tool_permission_rules_v9 RENAME TO tool_permission_rules")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tool_permission_rules_lookup
        ON tool_permission_rules(workspace_id, tool_name, rule_key, session_id)
        """
    )

def _ensure_tool_approval_audit_request_index(conn) -> None:
    """Prevent duplicate audit rows for one resolved approval request."""
    if not _table_exists(conn, "tool_approval_audit"):
        return
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_approval_audit_request
        ON tool_approval_audit(request_id)
        """
    )


def _downgrade_to_v9(conn) -> None:
    """Undo the v10 audit uniqueness migration while preserving approval data.

    v10 only adds a unique index on tool_approval_audit(request_id). A manual
    rollback to code that understands v9 can therefore drop that index and
    remove the v10 migration marker without deleting approval tables or rows.
    """
    conn.execute("DROP INDEX IF EXISTS idx_tool_approval_audit_request")
    conn.execute("DELETE FROM local_schema_migrations WHERE version=10")


def _table_exists(conn, table: str) -> bool:
    """Return whether a table exists in the current migration fixture."""
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    )

def _record_migration(conn, version: int, name: str) -> None:
    """Record one completed additive migration inside the caller transaction."""
    conn.execute(
        """
        INSERT INTO local_schema_migrations(version, name)
        VALUES (?, ?)
        ON CONFLICT(version) DO NOTHING
        """,
        (version, name),
    )


def _ensure_column(conn, table: str, column: str, declaration: str) -> None:
    """Add one trusted, statically declared column when it is absent."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if not existing:
        # Table does not exist; skip (e.g. in-memory test fixtures).
        return
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _ensure_context_window_lineage(conn) -> None:
    """Add immutable context summary lineage and deterministic message ordering."""
    has_messages = _table_exists(conn, "messages")
    has_sessions = _table_exists(conn, "sessions")
    _ensure_column(conn, "messages", "message_ordinal", "INTEGER")
    _ensure_column(conn, "sessions", "active_context_window_id", "TEXT")
    if not has_sessions:
        return
    conn.execute(
        """
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
        )
        """
    )
    if has_messages:
        conn.execute(
            """
            UPDATE messages
            SET message_ordinal = (
                SELECT COUNT(*)
                FROM messages AS earlier
                WHERE earlier.workspace_id = messages.workspace_id
                  AND earlier.session_id = messages.session_id
                  AND earlier.rowid <= messages.rowid
            )
            WHERE message_ordinal IS NULL
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_session
            ON messages(workspace_id, session_id, turn_index, message_ordinal)
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_session_ordinal_unique
            ON messages(workspace_id, session_id, message_ordinal)
            """
        )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_context_windows_session
        ON context_windows(workspace_id, session_id, created_at)
        """
    )
    session_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
    required_session_columns = {
        "workspace_id",
        "session_id",
        "active_branch_id",
        "summary",
        "summary_through_turn",
        "turn_index",
        "active_context_window_id",
    }
    if not required_session_columns.issubset(session_columns):
        return
    source_count_sql = (
        """
        (
            SELECT COUNT(*) FROM messages m
            WHERE m.workspace_id = s.workspace_id
              AND m.session_id = s.session_id
              AND m.turn_index <= COALESCE(s.summary_through_turn, 0)
        )
        """
        if has_messages
        else "0"
    )
    conn.execute(
        f"""
        INSERT OR IGNORE INTO context_windows(
            window_id, workspace_id, session_id, branch_id, first_window_id,
            previous_window_id, summary_text, summary_through_turn,
            compacted_from_turn, compacted_through_turn, opened_at_turn,
            source_message_count
        )
        SELECT
            'root-' || s.session_id,
            s.workspace_id,
            s.session_id,
            s.active_branch_id,
            'root-' || s.session_id,
            NULL,
            COALESCE(s.summary, ''),
            COALESCE(s.summary_through_turn, 0),
            CASE WHEN COALESCE(s.summary_through_turn, 0) > 0 THEN 1 ELSE 0 END,
            COALESCE(s.summary_through_turn, 0),
            COALESCE(s.turn_index, 0),
            {source_count_sql}
        FROM sessions s
        WHERE s.active_context_window_id IS NULL
        """
    )
    conn.execute(
        """
        UPDATE sessions
        SET active_context_window_id = 'root-' || session_id
        WHERE active_context_window_id IS NULL
        """
    )


def _ensure_state_validation_triggers(conn) -> None:
    """Add validation to existing tables that cannot gain CHECK constraints in place."""
    _create_validation_triggers(
        conn,
        "executions",
        "status",
        (
            "running",
            "paused_budget",
            "paused_error",
            "paused_confirmation",
            "paused_recovery",
            "unrecoverable_checkpoint",
            "completed",
            "discarded",
        ),
    )
    _create_validation_triggers(
        conn,
        "executions",
        "checkpoint_state",
        ("uninitialized", "available", "cleanup_pending", "cleaned", "missing"),
    )
    _create_validation_triggers(
        conn,
        "execution_slices",
        "status",
        (
            "running",
            "paused_budget",
            "paused_error",
            "paused_confirmation",
            "paused_recovery",
            "completed",
            "discarded",
        ),
    )
    _create_validation_triggers(
        conn,
        "maintenance_jobs",
        "status",
        ("pending", "running", "succeeded", "failed"),
    )
    _create_validation_triggers(
        conn,
        "execution_tasks",
        "status",
        ("pending", "in_progress", "completed", "cancelled"),
    )


def _ensure_execution_task_tables(conn) -> None:
    """Create private Execution task tables for existing local databases."""
    conn.execute(
        """
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
            UNIQUE(execution_id, task_id)
        )
        """
    )
    conn.execute(
        """
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
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_execution_tasks_execution
        ON execution_tasks(execution_id, ordinal, task_key)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_execution_task_dependencies_dep
        ON execution_task_dependencies(execution_id, depends_on_task_id)
        """
    )


def _create_validation_triggers(
    conn,
    table: str,
    column: str,
    allowed_values: tuple[str, ...],
) -> None:
    """Create trusted INSERT/UPDATE guards when the target table and column exist."""
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not table_exists:
        return
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        return
    allowed = ", ".join(f"'{value}'" for value in allowed_values)
    invalid = conn.execute(
        f"SELECT {column} FROM {table} WHERE {column} NOT IN ({allowed}) LIMIT 1"
    ).fetchone()
    if invalid:
        raise RuntimeError(
            f"Local state contains unsupported {table}.{column} value: {invalid[column]!r}"
        )
    for operation in ("INSERT", "UPDATE"):
        trigger = f"validate_{table}_{column}_{operation.lower()}"
        conn.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS {trigger}
            BEFORE {operation} ON {table}
            WHEN NEW.{column} NOT IN ({allowed})
            BEGIN
                SELECT RAISE(ABORT, 'invalid {table}.{column}');
            END
            """
        )
