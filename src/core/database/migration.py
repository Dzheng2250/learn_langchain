"""Explicit legacy-to-workspace database migration."""

import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from psycopg import sql

from src.config.paths import backup_dir
from src.config.settings import PG_DUMP_PATH, POSTGRES_DOCKER_CONTAINER
from src.core.database.connection import connection_kwargs
from src.core.database.queries import DETECT_LEGACY_SCHEMA, execute_sql_file
from src.core.workspace.resolver import canonical_path_key, canonicalize_workspace


@dataclass(frozen=True)
class MigrationReport:
    """Counts and backup metadata produced by inspect or apply."""
    sessions: int
    messages: int
    memories: int
    events: int
    workspace: Path
    applied: bool = False
    backup_path: Path | None = None


class WorkspaceMigration:
    """Migrate one selected legacy session into the workspace-aware schema."""

    LEGACY_TABLES = ("agent_sessions", "agent_messages", "agent_memories", "agent_events")

    def __init__(self, connection_factory) -> None:
        self.connection_factory = connection_factory

    def inspect(self, workspace: str | Path, keep_session: str) -> MigrationReport:
        """Count legacy rows that would be retained without modifying data."""
        root = canonicalize_workspace(workspace)
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                self._require_legacy(cur)
                cur.execute("SELECT count(*) FROM agent_sessions WHERE session_id = %s", (keep_session,))
                sessions = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM agent_messages WHERE session_id = %s", (keep_session,))
                messages = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM agent_events WHERE session_id = %s", (keep_session,))
                events = cur.fetchone()[0]
                cur.execute(
                    """
                    SELECT count(DISTINCT m.id)
                    FROM agent_memories m
                    JOIN LATERAL jsonb_array_elements_text(m.source_message_ids) source ON true
                    JOIN agent_messages msg ON msg.id = source.value::bigint
                    WHERE msg.session_id = %s
                    """,
                    (keep_session,),
                )
                memories = cur.fetchone()[0]
        if sessions != 1:
            raise RuntimeError(f"Expected exactly one legacy session named {keep_session!r}, found {sessions}.")
        return MigrationReport(sessions, messages, memories, events, root)

    def apply(self, workspace: str | Path, keep_session: str) -> MigrationReport:
        """Back up and atomically migrate one legacy Session into a Workspace."""
        report = self.inspect(workspace, keep_session)
        backup = create_database_backup()
        workspace_id = uuid4()
        session_id = uuid4()

        with self.connection_factory() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    self._require_legacy(cur)
                    for table in self.LEGACY_TABLES:
                        cur.execute(
                            sql.SQL("ALTER TABLE {} RENAME TO {}").format(
                                sql.Identifier(table),
                                sql.Identifier(f"legacy_{table}"),
                            )
                        )
                    execute_sql_file(cur, "schema.sql")
                    cur.execute(
                        """
                        INSERT INTO agent_workspaces(workspace_id, canonical_path, display_path)
                        VALUES (%s, %s, %s)
                        """,
                        (workspace_id, canonical_path_key(report.workspace), str(report.workspace)),
                    )
                    cur.execute(
                        """
                        INSERT INTO agent_sessions(
                            session_id, workspace_id, session_name, summary, recent_messages,
                            turn_index, created_at, updated_at
                        )
                        SELECT %s, %s, session_id, summary, recent_messages,
                               turn_index, created_at, updated_at
                        FROM legacy_agent_sessions WHERE session_id = %s
                        """,
                        (session_id, workspace_id, keep_session),
                    )
                    cur.execute(
                        """
                        INSERT INTO agent_messages(
                            id, workspace_id, session_id, role, content, message_type,
                            raw, turn_index, created_at
                        )
                        SELECT id, %s, %s, role, content, message_type, raw, turn_index, created_at
                        FROM legacy_agent_messages WHERE session_id = %s
                        """,
                        (workspace_id, session_id, keep_session),
                    )
                    cur.execute(
                        """
                        INSERT INTO agent_memories(
                            id, workspace_id, kind, content, tags, importance, confidence,
                            created_at, updated_at, archived_at
                        )
                        SELECT DISTINCT m.id, %s, m.kind, m.content, m.tags, m.importance,
                               m.confidence, m.created_at, m.updated_at, m.archived_at
                        FROM legacy_agent_memories m
                        JOIN LATERAL jsonb_array_elements_text(m.source_message_ids) source ON true
                        JOIN legacy_agent_messages msg ON msg.id = source.value::bigint
                        WHERE msg.session_id = %s
                        """,
                        (workspace_id, keep_session),
                    )
                    cur.execute(
                        """
                        INSERT INTO agent_memory_sources(workspace_id, memory_id, message_id)
                        SELECT DISTINCT %s, m.id, source.value::bigint
                        FROM legacy_agent_memories m
                        JOIN LATERAL jsonb_array_elements_text(m.source_message_ids) source ON true
                        JOIN agent_memories kept ON kept.id = m.id
                        JOIN agent_messages msg ON msg.id = source.value::bigint
                        """,
                        (workspace_id,),
                    )
                    cur.execute(
                        """
                        INSERT INTO agent_events(
                            id, run_id, workspace_id, session_id, turn_index, event_type,
                            source, level, message, payload, duration_ms, created_at
                        )
                        SELECT id, run_id, %s, %s, turn_index, event_type,
                               source, level, message, payload, duration_ms, created_at
                        FROM legacy_agent_events WHERE session_id = %s
                        """,
                        (workspace_id, session_id, keep_session),
                    )
                    cur.execute(
                        "SELECT setval(pg_get_serial_sequence('agent_messages','id'), "
                        "COALESCE((SELECT max(id) FROM agent_messages), 1), true)"
                    )
                    cur.execute(
                        "SELECT setval(pg_get_serial_sequence('agent_events','id'), "
                        "COALESCE((SELECT max(id) FROM agent_events), 1), true)"
                    )
                    self._validate_counts(cur, report)
                    for table in self.LEGACY_TABLES:
                        cur.execute(
                            sql.SQL("DROP TABLE {}").format(sql.Identifier(f"legacy_{table}"))
                        )
                    # Re-run idempotent DDL after legacy indexes are gone. Explicitly named
                    # legacy indexes can otherwise cause CREATE INDEX IF NOT EXISTS to skip.
                    execute_sql_file(cur, "schema.sql")
                    cur.execute(
                        """
                        INSERT INTO schema_migrations(version, name)
                        VALUES (2, 'workspace_isolation_current')
                        ON CONFLICT (version) DO NOTHING
                        """
                    )

        return MigrationReport(
            report.sessions,
            report.messages,
            report.memories,
            report.events,
            report.workspace,
            applied=True,
            backup_path=backup,
        )

    def _require_legacy(self, cur) -> None:
        """Reject migration unless the connected database has the legacy shape."""
        cur.execute(DETECT_LEGACY_SCHEMA)
        is_current, has_sessions = cur.fetchone()
        if not has_sessions or is_current:
            raise RuntimeError("The database is not an unmigrated legacy schema.")

    def _validate_counts(self, cur, expected: MigrationReport) -> None:
        """Abort the transaction when migrated row counts differ from dry-run."""
        for table, count in (
            ("agent_sessions", expected.sessions),
            ("agent_messages", expected.messages),
            ("agent_memories", expected.memories),
            ("agent_events", expected.events),
        ):
            cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
            actual = cur.fetchone()[0]
            if actual != count:
                raise RuntimeError(f"Migration validation failed for {table}: expected {count}, got {actual}.")


def create_database_backup() -> Path:
    """Create a complete custom-format dump before destructive migration."""
    target_dir = backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"learn_agent_{datetime.now():%Y%m%d_%H%M%S}.dump"
    config = connection_kwargs()
    host = str(config.get("host", "127.0.0.1"))
    port = str(config.get("port", 5432))
    user = str(config.get("user", "postgres"))
    dbname = str(config.get("dbname", "learn_agent"))
    password = str(config.get("password") or "")
    native = PG_DUMP_PATH or shutil.which("pg_dump")
    if native:
        command = [
            native,
            "-Fc",
            "-h",
            host,
            "-p",
            port,
            "-U",
            user,
            "-d",
            dbname,
            "-f",
            str(target),
        ]
        env = os.environ.copy()
        if password:
            env["PGPASSWORD"] = password
        subprocess.run(command, check=True, env=env, capture_output=True)
    elif shutil.which("docker"):
        docker_command = ["docker", "exec"]
        if password:
            docker_command.extend(["-e", f"PGPASSWORD={password}"])
        docker_command.extend(
            [
                POSTGRES_DOCKER_CONTAINER,
                "pg_dump",
                "-Fc",
                "-U",
                user,
                "-d",
                dbname,
            ]
        )
        with target.open("wb") as output:
            subprocess.run(
                docker_command,
                check=True,
                stdout=output,
                stderr=subprocess.PIPE,
            )
    else:
        raise RuntimeError("No pg_dump executable or Docker fallback is available.")
    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError("Database backup failed or produced an empty file.")
    return target
