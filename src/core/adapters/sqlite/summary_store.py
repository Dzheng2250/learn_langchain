"""SQLite adapter for derived conversation summary lineage."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import uuid4

from langchain_core.messages import messages_from_dict

from src.core.telemetry import emit_event
from src.core.context.models import ContextWindowSource, TurnChunk
from src.core.adapters.sqlite.session_store import serialize_recent_turns
from src.core.adapters.sqlite.message_lineage import active_lineage_rows

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
    ) -> ContextWindowSource:
        """Load committed messages newer than the active context window."""
        with self.database.connect() as conn:
            window = self._ensure_active_window(conn, session)
            rows = active_lineage_rows(
                conn,
                workspace_id=str(session.workspace.workspace_id),
                session_id=str(session.session_id),
                after_turn=int(window["summary_through_turn"]),
                through_turn=target_turn,
            )
        turns: list[TurnChunk] = []
        message_ids: list[str] = []
        for row, message in zip(
            rows,
            messages_from_dict([json.loads(row["raw"]) for row in rows]),
            strict=True,
        ):
            turn_index = int(row["turn_index"])
            if not turns or turns[-1].turn_index != turn_index:
                turns.append(TurnChunk(turn_index, [message]))
            else:
                turns[-1].messages.append(message)
            message_ids.append(str(row["message_id"]))
        return ContextWindowSource(
            window_id=str(window["window_id"]),
            summary=window["summary_text"] or "",
            summary_through_turn=int(window["summary_through_turn"]),
            turns=tuple(turns),
            message_ids=tuple(message_ids),
        )

    def update_summary_cas(
        self,
        session: SessionContext,
        *,
        expected_window_id: str | None = None,
        expected_summary_through_turn: int | None = None,
        summary_through_turn: int,
        summary: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model: str = "",
    ) -> bool:
        """Create a new context window when the active window is still current."""
        if expected_summary_through_turn is None:
            expected_summary_through_turn = -1
        if summary_through_turn <= expected_summary_through_turn:
            return True
        with self.database.transaction() as conn:
            current = self._get_active_window(conn, session)
            if current is None:
                raise RuntimeError(
                    "Session has no active context window before summary maintenance."
                )
            if expected_window_id and str(current["window_id"]) != expected_window_id:
                return False
            if (
                not expected_window_id
                and int(current["summary_through_turn"]) != expected_summary_through_turn
            ):
                return False
            expected_summary_through_turn = int(current["summary_through_turn"])
            source_rows = active_lineage_rows(
                conn,
                workspace_id=str(session.workspace.workspace_id),
                session_id=str(session.session_id),
                after_turn=expected_summary_through_turn,
                through_turn=summary_through_turn,
            )
            source_count = len(source_rows)
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
                    source_message_count, input_tokens, output_tokens, model
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    max(0, int(input_tokens)),
                    max(0, int(output_tokens)),
                    str(model or ""),
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
            tail_rows = active_lineage_rows(
                conn,
                workspace_id=str(session.workspace.workspace_id),
                session_id=str(session.session_id),
                after_turn=summary_through_turn,
            )
            retained: list[TurnChunk] = []
            for row, message in zip(
                tail_rows,
                messages_from_dict([json.loads(row["raw"]) for row in tail_rows]),
                strict=True,
            ):
                turn_index = int(row["turn_index"])
                if not retained or retained[-1].turn_index != turn_index:
                    retained.append(TurnChunk(turn_index, [message]))
                else:
                    retained[-1].messages.append(message)
            cur = conn.execute(
                """
                UPDATE sessions
                SET active_context_window_id=?, summary=?, summary_through_turn=?,
                    recent_messages=?, version=version + 1,
                    updated_at=CURRENT_TIMESTAMP
                WHERE workspace_id=? AND session_id=? AND active_context_window_id=?
                """,
                (
                    window_id,
                    summary,
                    summary_through_turn,
                    serialize_recent_turns(retained),
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
