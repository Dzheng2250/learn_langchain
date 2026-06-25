"""Copy retained PostgreSQL rows into the local SQLite state database."""

import json
from uuid import NAMESPACE_URL, uuid5

from src.core.state.database import LocalStateDatabase
from src.core.state.migration_models import LocalStateMigrationReport
from src.core.workspace.resolver import canonical_path_key


class LocalStateMigrationCopier:
    """Move one retained Session and its owned rows into local state.

    The class owns row-level copy details so ``LocalStateMigration`` can remain a
    high-level migration orchestrator.
    """

    def __init__(self, connection_factory) -> None:
        self.connection_factory = connection_factory

    def copy(self, report: LocalStateMigrationReport, database: LocalStateDatabase) -> None:
        """Copy workspace, session, messages, memories, and imported events."""
        with self.connection_factory() as source:
            with source.cursor() as cur:
                session = self._copy_workspace_and_session(cur, report, database)
                message_map = self._copy_messages(cur, report, database, session)
                self._copy_memories(cur, report, database, session, message_map)
                self._copy_events(cur, database, session)

    def _copy_workspace_and_session(self, cur, report, database) -> dict:
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
        row = cur.fetchone()
        workspace_id, canonical, display, session_id = row[:4]
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
                    row[4] or "",
                    json.dumps(row[5], ensure_ascii=False, default=str),
                    int(row[6]),
                    int(row[6]),
                    branch_id,
                    str(row[7]),
                    str(row[8]),
                ),
            )
            target.execute(
                """
                INSERT INTO branches(branch_id, workspace_id, session_id, branch_name)
                VALUES (?, ?, ?, 'main')
                """,
                (branch_id, workspace_id, session_id),
            )
        return {
            "workspace_id": workspace_id,
            "session_id": session_id,
            "branch_id": branch_id,
        }

    def _copy_messages(self, cur, report, database, session: dict) -> dict[int, str]:
        cur.execute(
            """
            SELECT id, role, content, message_type, raw, turn_index, created_at
            FROM agent_messages WHERE session_id = %s
            ORDER BY turn_index, id
            """,
            (session["session_id"],),
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
                        parent_message_id, role, content, message_type, raw, turn_index,
                        message_ordinal, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        legacy_id,
                        session["workspace_id"],
                        session["session_id"],
                        session["branch_id"],
                        parent,
                        row[1],
                        row[2] or "",
                        row[3],
                        json.dumps(row[4], ensure_ascii=False, default=str),
                        int(row[5]),
                        len(message_map),
                        str(row[6]),
                    ),
                )
                parent = message_id
            target.execute(
                "UPDATE branches SET head_message_id = ?, version = ? WHERE branch_id = ?",
                (parent, report.messages, session["branch_id"]),
            )
        return message_map

    def _copy_memories(self, cur, report, database, session: dict, message_map: dict[int, str]) -> None:
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
            (session["session_id"],),
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
                        session["workspace_id"],
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
            self._copy_memory_sources(cur, target, session, message_map)

    def _copy_memory_sources(self, cur, target, session: dict, message_map: dict[int, str]) -> None:
        cur.execute(
            """
            SELECT src.memory_id::text, src.message_id
            FROM agent_memory_sources src
            JOIN agent_messages msg
              ON msg.workspace_id = src.workspace_id AND msg.id = src.message_id
            WHERE msg.session_id = %s
            """,
            (session["session_id"],),
        )
        for memory_id, legacy_message_id in cur.fetchall():
            if int(legacy_message_id) in message_map:
                target.execute(
                    """
                    INSERT INTO memory_sources(workspace_id, memory_id, message_id)
                    VALUES (?, ?, ?)
                    ON CONFLICT DO NOTHING
                    """,
                    (session["workspace_id"], memory_id, message_map[int(legacy_message_id)]),
                )

    def _copy_events(self, cur, database, session: dict) -> None:
        cur.execute(
            """
            SELECT id, run_id, workspace_id::text, session_id::text, turn_index,
                   event_type, source, level, message, payload, duration_ms, created_at
            FROM agent_events WHERE session_id = %s ORDER BY id
            """,
            (session["session_id"],),
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

