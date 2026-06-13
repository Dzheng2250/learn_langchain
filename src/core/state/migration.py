"""Explicit PostgreSQL-to-local-state migration for one retained Session."""

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from src.config.paths import local_state_db
from src.core.database.migration import create_database_backup
from src.core.state.database import LocalStateDatabase
from src.core.workspace.resolver import canonical_path_key, canonicalize_workspace


@dataclass(frozen=True)
class LocalStateMigrationReport:
    """Counts and paths produced by a local-state migration inspection or apply."""

    workspace: Path
    session_name: str
    sessions: int
    messages: int
    memories: int
    events: int
    target_path: Path
    source_sessions: int = 0
    source_messages: int = 0
    source_memories: int = 0
    source_events: int = 0
    applied: bool = False
    source_pruned: bool = False
    backup_path: Path | None = None

    @property
    def deleted_counts(self) -> dict[str, int]:
        """Return the source rows removed when pruning to the retained Session."""
        return {
            "sessions": max(0, self.source_sessions - self.sessions),
            "messages": max(0, self.source_messages - self.messages),
            "memories": max(0, self.source_memories - self.memories),
            "events": max(0, self.source_events - self.events),
        }


class LocalStateMigration:
    """Import one Workspace-local PostgreSQL Session into a new SQLite authority."""

    def __init__(self, connection_factory, target_path: str | Path | None = None) -> None:
        self.connection_factory = connection_factory
        self.target_path = Path(target_path or local_state_db()).expanduser().resolve()

    def inspect(self, workspace: str | Path, keep_session: str = "default") -> LocalStateMigrationReport:
        """Read retained source counts without creating or modifying local state."""
        root = canonicalize_workspace(workspace)
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.workspace_id, s.session_id
                    FROM agent_sessions s
                    JOIN agent_workspaces w ON w.workspace_id = s.workspace_id
                    WHERE w.canonical_path = %s AND s.session_name = %s
                    """,
                    (canonical_path_key(root), keep_session),
                )
                identity = cur.fetchone()
                if not identity:
                    raise RuntimeError(
                        f"No PostgreSQL Session named {keep_session!r} exists for Workspace {root}."
                    )
                workspace_id, session_id = identity
                cur.execute("SELECT count(*) FROM agent_messages WHERE session_id = %s", (session_id,))
                messages = int(cur.fetchone()[0])
                cur.execute(
                    """
                    SELECT count(DISTINCT m.id)
                    FROM agent_memories m
                    JOIN agent_memory_sources src
                      ON src.workspace_id = m.workspace_id AND src.memory_id = m.id
                    JOIN agent_messages msg
                      ON msg.workspace_id = src.workspace_id AND msg.id = src.message_id
                    WHERE msg.session_id = %s
                    """,
                    (session_id,),
                )
                memories = int(cur.fetchone()[0])
                cur.execute("SELECT count(*) FROM agent_events WHERE session_id = %s", (session_id,))
                events = int(cur.fetchone()[0])
                source_counts = {}
                for name, table in (
                    ("sessions", "agent_sessions"),
                    ("messages", "agent_messages"),
                    ("memories", "agent_memories"),
                    ("events", "agent_events"),
                ):
                    cur.execute(f"SELECT count(*) FROM {table}")
                    source_counts[name] = int(cur.fetchone()[0])
        return LocalStateMigrationReport(
            root,
            keep_session,
            1,
            messages,
            memories,
            events,
            self.target_path,
            source_sessions=source_counts["sessions"],
            source_messages=source_counts["messages"],
            source_memories=source_counts["memories"],
            source_events=source_counts["events"],
        )

    def apply(
        self,
        workspace: str | Path,
        keep_session: str = "default",
        *,
        prune_source: bool = False,
    ) -> LocalStateMigrationReport:
        """Back up PostgreSQL, build validated SQLite state, and optionally prune source."""
        report = self.inspect(workspace, keep_session)
        backup = create_database_backup()
        temporary = self.target_path.with_suffix(".migration.tmp")
        temporary.unlink(missing_ok=True)
        database = LocalStateDatabase(temporary)
        database.initialize()
        try:
            self._copy(report, database)
            self._validate(report, database)
            self.target_path.parent.mkdir(parents=True, exist_ok=True)
            if self.target_path.exists():
                existing_backup = self.target_path.with_suffix(".pre_migration.bak")
                shutil.copy2(self.target_path, existing_backup)
            os.replace(temporary, self.target_path)
            if prune_source:
                self._prune_source(report)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return LocalStateMigrationReport(
            report.workspace,
            report.session_name,
            report.sessions,
            report.messages,
            report.memories,
            report.events,
            report.target_path,
            source_sessions=report.source_sessions,
            source_messages=report.source_messages,
            source_memories=report.source_memories,
            source_events=report.source_events,
            applied=True,
            source_pruned=prune_source,
            backup_path=backup,
        )

    def _prune_source(self, report: LocalStateMigrationReport) -> None:
        """Delete every PostgreSQL business row unrelated to the retained Session.

        This operation is intentionally separate from the SQLite copy. It runs
        only after the local authority has passed validation and uses one
        PostgreSQL transaction so a failed count check rolls back all deletes.
        """
        with self.connection_factory() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT s.workspace_id, s.session_id
                        FROM agent_sessions s
                        JOIN agent_workspaces w ON w.workspace_id = s.workspace_id
                        WHERE w.canonical_path = %s AND s.session_name = %s
                        """,
                        (canonical_path_key(report.workspace), report.session_name),
                    )
                    identity = cur.fetchone()
                    if not identity:
                        raise RuntimeError("Retained PostgreSQL Session disappeared before pruning.")
                    workspace_id, session_id = identity

                    # Remove source links from other Sessions first. Memories
                    # are retained only when at least one source is a message
                    # belonging to the selected default Session.
                    cur.execute(
                        """
                        DELETE FROM agent_memory_sources src
                        WHERE NOT EXISTS (
                            SELECT 1 FROM agent_messages msg
                            WHERE msg.workspace_id = src.workspace_id
                              AND msg.id = src.message_id
                              AND msg.session_id = %s
                        )
                        """,
                        (session_id,),
                    )
                    cur.execute(
                        """
                        DELETE FROM agent_memories m
                        WHERE m.workspace_id <> %s OR NOT EXISTS (
                            SELECT 1 FROM agent_memory_sources src
                            WHERE src.workspace_id = m.workspace_id
                              AND src.memory_id = m.id
                        )
                        """,
                        (workspace_id,),
                    )
                    # Session deletion cascades its messages and Session-bound
                    # events. Lifecycle events have no Session and are not part
                    # of the retained default conversation.
                    cur.execute(
                        "DELETE FROM agent_events WHERE session_id IS NULL OR session_id <> %s",
                        (session_id,),
                    )
                    cur.execute("DELETE FROM agent_sessions WHERE session_id <> %s", (session_id,))
                    cur.execute("DELETE FROM agent_workspaces WHERE workspace_id <> %s", (workspace_id,))
                    self._validate_source_prune(cur, report)

    def _validate_source_prune(self, cur, expected: LocalStateMigrationReport) -> None:
        """Abort source pruning unless retained PostgreSQL counts match SQLite."""
        for table, count in (
            ("agent_sessions", expected.sessions),
            ("agent_messages", expected.messages),
            ("agent_memories", expected.memories),
            ("agent_events", expected.events),
        ):
            cur.execute(f"SELECT count(*) FROM {table}")
            actual = int(cur.fetchone()[0])
            if actual != count:
                raise RuntimeError(
                    f"PostgreSQL prune validation failed for {table}: "
                    f"expected {count}, got {actual}."
                )

    def _copy(self, report: LocalStateMigrationReport, database: LocalStateDatabase) -> None:
        with self.connection_factory() as source:
            with source.cursor() as cur:
                cur.execute(
                    """
                    SELECT w.workspace_id::text, w.canonical_path, w.display_path,
                           s.session_id::text, s.summary, s.recent_messages,
                           s.turn_index, s.created_at, s.updated_at
                    FROM agent_sessions s
                    JOIN agent_workspaces w ON w.workspace_id = s.workspace_id
                    WHERE w.canonical_path = %s AND s.session_name = %s
                    """,
                    (canonical_path_key(report.workspace), report.session_name),
                )
                session_row = cur.fetchone()
                workspace_id, canonical, display, session_id = session_row[:4]
                branch_id = str(uuid5(NAMESPACE_URL, f"learn-agent:{session_id}:main"))
                with database.transaction() as target:
                    target.execute(
                        """
                        INSERT INTO workspaces(workspace_id, canonical_path, display_path)
                        VALUES (?, ?, ?)
                        """,
                        (workspace_id, canonical, display),
                    )
                    target.execute(
                        """
                        INSERT INTO sessions(
                            session_id, workspace_id, session_name, summary, recent_messages,
                            turn_index, version, active_branch_id, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session_id,
                            workspace_id,
                            report.session_name,
                            session_row[4] or "",
                            json.dumps(session_row[5], ensure_ascii=False, default=str),
                            int(session_row[6]),
                            int(session_row[6]),
                            branch_id,
                            str(session_row[7]),
                            str(session_row[8]),
                        ),
                    )
                    target.execute(
                        """
                        INSERT INTO branches(branch_id, workspace_id, session_id, branch_name)
                        VALUES (?, ?, ?, 'main')
                        """,
                        (branch_id, workspace_id, session_id),
                    )

                cur.execute(
                    """
                    SELECT id, role, content, message_type, raw, turn_index, created_at
                    FROM agent_messages WHERE session_id = %s
                    ORDER BY turn_index, id
                    """,
                    (session_id,),
                )
                message_map, parent = {}, None
                with database.transaction() as target:
                    for row in cur.fetchall():
                        legacy_id = int(row[0])
                        message_id = str(uuid5(NAMESPACE_URL, f"learn-agent:legacy-message:{legacy_id}"))
                        message_map[legacy_id] = message_id
                        target.execute(
                            """
                            INSERT INTO messages(
                                message_id, legacy_message_id, workspace_id, session_id, branch_id,
                                parent_message_id, role, content, message_type, raw, turn_index, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                message_id,
                                legacy_id,
                                workspace_id,
                                session_id,
                                branch_id,
                                parent,
                                row[1],
                                row[2] or "",
                                row[3],
                                json.dumps(row[4], ensure_ascii=False, default=str),
                                int(row[5]),
                                str(row[6]),
                            ),
                        )
                        parent = message_id
                    target.execute(
                        "UPDATE branches SET head_message_id = ?, version = ? WHERE branch_id = ?",
                        (parent, report.messages, branch_id),
                    )

                cur.execute(
                    """
                    SELECT DISTINCT m.id::text, m.kind, m.content, m.tags, m.importance,
                           m.confidence, m.created_at, m.updated_at, m.archived_at
                    FROM agent_memories m
                    JOIN agent_memory_sources src
                      ON src.workspace_id = m.workspace_id AND src.memory_id = m.id
                    JOIN agent_messages msg
                      ON msg.workspace_id = src.workspace_id AND msg.id = src.message_id
                    WHERE msg.session_id = %s
                    """,
                    (session_id,),
                )
                memory_rows = cur.fetchall()
                with database.transaction() as target:
                    for row in memory_rows:
                        target.execute(
                            """
                            INSERT INTO memories(
                                memory_id, workspace_id, kind, content, tags, importance,
                                confidence, created_at, updated_at, archived_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                row[0],
                                workspace_id,
                                row[1],
                                row[2],
                                json.dumps(row[3], ensure_ascii=False, default=str),
                                int(row[4]),
                                float(row[5]),
                                str(row[6]),
                                str(row[7]),
                                str(row[8]) if row[8] else None,
                            ),
                        )
                    cur.execute(
                        """
                        SELECT src.memory_id::text, src.message_id
                        FROM agent_memory_sources src
                        JOIN agent_messages msg
                          ON msg.workspace_id = src.workspace_id AND msg.id = src.message_id
                        WHERE msg.session_id = %s
                        """,
                        (session_id,),
                    )
                    for memory_id, legacy_message_id in cur.fetchall():
                        if int(legacy_message_id) in message_map:
                            target.execute(
                                """
                                INSERT INTO memory_sources(workspace_id, memory_id, message_id)
                                VALUES (?, ?, ?)
                                ON CONFLICT DO NOTHING
                                """,
                                (workspace_id, memory_id, message_map[int(legacy_message_id)]),
                            )

                cur.execute(
                    """
                    SELECT id, run_id, workspace_id::text, session_id::text, turn_index,
                           event_type, source, level, message, payload, duration_ms, created_at
                    FROM agent_events WHERE session_id = %s ORDER BY id
                    """,
                    (session_id,),
                )
                with database.transaction() as target:
                    for row in cur.fetchall():
                        target.execute(
                            """
                            INSERT INTO imported_events(
                                source_event_id, run_id, workspace_id, session_id, turn_index,
                                event_type, source, level, message, payload, duration_ms, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                int(row[0]),
                                row[1],
                                row[2],
                                row[3],
                                row[4],
                                row[5],
                                row[6],
                                row[7],
                                row[8],
                                json.dumps(row[9], ensure_ascii=False, default=str),
                                row[10],
                                str(row[11]),
                            ),
                        )

    def _validate(self, expected: LocalStateMigrationReport, database: LocalStateDatabase) -> None:
        with database.connect() as conn:
            actual = {
                "sessions": conn.execute("SELECT count(*) FROM sessions").fetchone()[0],
                "messages": conn.execute("SELECT count(*) FROM messages").fetchone()[0],
                "memories": conn.execute("SELECT count(*) FROM memories").fetchone()[0],
                "events": conn.execute("SELECT count(*) FROM imported_events").fetchone()[0],
            }
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Local-state migration produced foreign-key violations: {violations[:3]}")
        for name in actual:
            wanted = getattr(expected, name)
            if actual[name] != wanted:
                raise RuntimeError(
                    f"Local-state migration validation failed for {name}: expected {wanted}, got {actual[name]}."
                )
