"""Idempotent additive migrations for the authoritative local state database."""


LATEST_SCHEMA_VERSION = 3


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
