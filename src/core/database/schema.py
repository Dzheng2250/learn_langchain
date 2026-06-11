"""Schema initialization, version upgrades, and legacy-schema protection."""

from src.core.database.queries import (
    DETECT_LEGACY_SCHEMA,
    HAS_SCHEMA_MIGRATIONS,
    INSERT_SCHEMA_MIGRATION,
    SELECT_SCHEMA_VERSION,
    execute_sql_file,
)


CURRENT_SCHEMA_VERSION = 2
MIGRATIONS = {
    2: ("memory_source_workspace", "002_memory_source_workspace.sql"),
}


class LegacySchemaError(RuntimeError):
    """Raised when explicit migration is required before Core can start."""


class SchemaManager:
    def __init__(self, pool) -> None:
        self.pool = pool

    def initialize(self) -> None:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(DETECT_LEGACY_SCHEMA)
                    is_current, has_sessions = cur.fetchone()
                    if has_sessions and not is_current:
                        raise LegacySchemaError(
                            "Legacy database schema detected. Stop Core and run "
                            "'learn-agent-core migrate-workspace --workspace <path> "
                            "--keep-session default' before starting the daemon."
                        )
                    if not has_sessions:
                        execute_sql_file(cur, "schema.sql")
                        cur.execute(
                            INSERT_SCHEMA_MIGRATION,
                            (CURRENT_SCHEMA_VERSION, "workspace_isolation_current"),
                        )
                    else:
                        cur.execute(HAS_SCHEMA_MIGRATIONS)
                        if not cur.fetchone()[0]:
                            raise LegacySchemaError(
                                "Current-looking database has no migration version. "
                                "Refusing automatic upgrade."
                            )
                        cur.execute(SELECT_SCHEMA_VERSION)
                        version = int(cur.fetchone()[0])
                        if version < 1:
                            raise LegacySchemaError(
                                "Database migration version is missing or invalid. "
                                "Refusing automatic upgrade."
                            )
                        if version > CURRENT_SCHEMA_VERSION:
                            raise LegacySchemaError(
                                f"Database schema version {version} is newer than this Core "
                                f"supports ({CURRENT_SCHEMA_VERSION}). Refusing to start."
                            )
                        for target_version, (name, filename) in MIGRATIONS.items():
                            if version < target_version:
                                execute_sql_file(cur, filename)
                                cur.execute(INSERT_SCHEMA_MIGRATION, (target_version, name))
                        execute_sql_file(cur, "schema.sql")
