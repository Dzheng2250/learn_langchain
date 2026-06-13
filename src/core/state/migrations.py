"""Idempotent additive migrations for the authoritative local state database."""


LATEST_SCHEMA_VERSION = 2


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
    conn.execute(
        """
        INSERT INTO local_schema_migrations(version, name)
        VALUES (?, ?)
        ON CONFLICT(version) DO NOTHING
        """,
        (LATEST_SCHEMA_VERSION, "durable_maintenance_and_checkpoint_state"),
    )


def _ensure_column(conn, table: str, column: str, declaration: str) -> None:
    """Add one trusted, statically declared column when it is absent."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
