"""Idempotent additive migrations for the authoritative local state database."""


LATEST_SCHEMA_VERSION = 8


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
    if current_version < 2:
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
    if current_version < 3:
        _ensure_state_validation_triggers(conn)
        _record_migration(conn, 3, "typed_domain_state_validation")
    if current_version < 4:
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
    if current_version < 5:
        _ensure_column(
            conn,
            "sessions",
            "context_tokens",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _record_migration(conn, 5, "session_context_tokens")
    if current_version < 6:
        _ensure_column(conn, "sessions", "archived_at", "TEXT")
        _record_migration(conn, 6, "session_archive_marker")
    if current_version < 7:
        _ensure_context_window_lineage(conn)
        _record_migration(conn, 7, "context_window_lineage")
    if current_version < 8:
        _ensure_tool_approval_tables(conn)
        _record_migration(conn, 8, "tool_approval_policy")


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
