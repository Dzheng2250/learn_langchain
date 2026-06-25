"""SQLite adapter for derived conversation summary lineage."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import uuid4

from langchain_core.messages import messages_from_dict

from src.core.telemetry import emit_event

if TYPE_CHECKING:
    from src.core.state.database import LocalStateDatabase
    from src.core.workspace.models import SessionContext


class SQLiteSummaryStore:
    """Read and advance immutable Session context windows."""

    def __init__(self, database: LocalStateDatabase) -> None:
        self.database = database

    def load_summary_source(
        self,
        session: SessionContext,
        target_turn: int,
    ) -> tuple[str, int, list[tuple[int, object]]]:
        """Load committed messages newer than the active context window."""
        with self.database.connect() as conn:
            window = self._ensure_active_window(conn, session)
            rows = conn.execute(
                """
                SELECT turn_index, raw FROM messages
                WHERE workspace_id=? AND session_id=?
                  AND turn_index > ? AND turn_index <= ?
                ORDER BY turn_index, message_ordinal
                """,
                (
                    str(session.workspace.workspace_id),
                    str(session.session_id),
                    int(window["summary_through_turn"]),
                    target_turn,
                ),
            ).fetchall()
        return (
            window["summary_text"] or "",
            int(window["summary_through_turn"]),
            [
                (int(row["turn_index"]), message)
                for row, message in zip(
                    rows,
                    messages_from_dict([json.loads(row["raw"]) for row in rows]),
                    strict=True,
                )
            ],
        )

    def update_summary_cas(
        self,
        session: SessionContext,
        *,
        expected_summary_through_turn: int,
        summary_through_turn: int,
        summary: str,
    ) -> bool:
        """Create a new context window when the active window is still current."""
        if summary_through_turn <= expected_summary_through_turn:
            return True
        with self.database.transaction() as conn:
            current = self._get_active_window(conn, session)
            if current is None:
                raise RuntimeError(
                    "Session has no active context window before summary maintenance."
                )
            if int(current["summary_through_turn"]) != expected_summary_through_turn:
                return False
            source_count = conn.execute(
                """
                SELECT COUNT(*) AS count FROM messages
                WHERE workspace_id=? AND session_id=?
                  AND turn_index > ? AND turn_index <= ?
                """,
                (
                    str(session.workspace.workspace_id),
                    str(session.session_id),
                    expected_summary_through_turn,
                    summary_through_turn,
                ),
            ).fetchone()["count"]
            session_row = conn.execute(
                """
                SELECT turn_index, active_branch_id FROM sessions
                WHERE workspace_id=? AND session_id=?
                """,
                (str(session.workspace.workspace_id), str(session.session_id)),
            ).fetchone()
            window_id = str(uuid4())
            first_window_id = current["first_window_id"] or current["window_id"]
            conn.execute(
                """
                INSERT INTO context_windows(
                    window_id, workspace_id, session_id, branch_id, first_window_id,
                    previous_window_id, summary_text, summary_through_turn,
                    compacted_from_turn, compacted_through_turn, opened_at_turn,
                    source_message_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    window_id,
                    str(session.workspace.workspace_id),
                    str(session.session_id),
                    session_row["active_branch_id"],
                    first_window_id,
                    current["window_id"],
                    summary,
                    summary_through_turn,
                    expected_summary_through_turn + 1,
                    summary_through_turn,
                    int(session_row["turn_index"]),
                    int(source_count),
                ),
            )
            conn.execute(
                """
                UPDATE context_windows
                SET closed_at_turn=?
                WHERE window_id=? AND closed_at_turn IS NULL
                """,
                (summary_through_turn, current["window_id"]),
            )
            cur = conn.execute(
                """
                UPDATE sessions
                SET active_context_window_id=?, summary=?, summary_through_turn=?,
                    version=version + 1, updated_at=CURRENT_TIMESTAMP
                WHERE workspace_id=? AND session_id=? AND active_context_window_id=?
                """,
                (
                    window_id,
                    summary,
                    summary_through_turn,
                    str(session.workspace.workspace_id),
                    str(session.session_id),
                    current["window_id"],
                ),
            )
        return cur.rowcount == 1

    def _get_active_window(self, conn, session: SessionContext):
        """Return the active window row without repairing missing lineage."""
        return conn.execute(
            """
            SELECT cw.* FROM sessions s
            JOIN context_windows cw ON cw.window_id = s.active_context_window_id
            WHERE s.workspace_id=? AND s.session_id=?
            """,
            (str(session.workspace.workspace_id), str(session.session_id)),
        ).fetchone()

    def _ensure_active_window(self, conn, session: SessionContext):
        """Return the active window, repairing legacy rows that lack one."""
        row = self._get_active_window(conn, session)
        if row:
            return row
        session_row = conn.execute(
            """
            SELECT session_id, workspace_id, active_branch_id, summary,
                   summary_through_turn, turn_index
            FROM sessions WHERE workspace_id=? AND session_id=?
            """,
            (str(session.workspace.workspace_id), str(session.session_id)),
        ).fetchone()
        if not session_row:
            raise RuntimeError("Session disappeared before context summary maintenance.")
        window_id = f"root-{session_row['session_id']}"
        emit_event(
            "context_window_repaired",
            "sqlite_summary_store",
            "Created a missing root context window for a legacy session row.",
            {
                "workspace_id": str(session.workspace.workspace_id),
                "session_id": str(session.session_id),
                "window_id": window_id,
            },
            level="warning",
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO context_windows(
                window_id, workspace_id, session_id, branch_id, first_window_id,
                previous_window_id, summary_text, summary_through_turn,
                compacted_from_turn, compacted_through_turn, opened_at_turn,
                source_message_count
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                window_id,
                session_row["workspace_id"],
                session_row["session_id"],
                session_row["active_branch_id"],
                window_id,
                session_row["summary"] or "",
                int(session_row["summary_through_turn"] or 0),
                1 if int(session_row["summary_through_turn"] or 0) > 0 else 0,
                int(session_row["summary_through_turn"] or 0),
                int(session_row["turn_index"] or 0),
                0,
            ),
        )
        conn.execute(
            """
            UPDATE sessions SET active_context_window_id=?
            WHERE workspace_id=? AND session_id=? AND active_context_window_id IS NULL
            """,
            (window_id, str(session.workspace.workspace_id), str(session.session_id)),
        )
        return conn.execute(
            "SELECT * FROM context_windows WHERE window_id=?",
            (window_id,),
        ).fetchone()
