"""Idempotent additive migrations for the authoritative local state database."""


LATEST_SCHEMA_VERSION = 5


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
        # Table does not exist — skip (e.g. in-memory test fixtures)
        return
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


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
