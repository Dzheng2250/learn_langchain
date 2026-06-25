"""SQLite Session context adapter."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from langchain_core.messages import messages_from_dict, messages_to_dict

from src.core.context.models import AgentContextState

if TYPE_CHECKING:
    from src.core.finalization.models import CompletedTurn
    from src.core.state.database import LocalStateDatabase


class SQLiteSessionStore:
    """Read Session context and optionally update it inside a Unit of Work."""

    def __init__(
        self,
        database: LocalStateDatabase,
        *,
        transaction_conn=None,
    ) -> None:
        self.database = database
        self._transaction_conn = transaction_conn

    def load_context(self, session) -> tuple[AgentContextState, int]:
        """Return compact context and latest committed Turn index."""
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT s.summary, s.recent_messages, s.context_tokens, s.turn_index,
                       cw.summary_text AS window_summary
                FROM sessions s
                LEFT JOIN context_windows cw ON cw.window_id = s.active_context_window_id
                WHERE s.workspace_id = ? AND s.session_id = ?
                """,
                (str(session.workspace.workspace_id), str(session.session_id)),
            ).fetchone()
        if not row:
            raise RuntimeError("Resolved session disappeared before it could be loaded.")
        recent = json.loads(row["recent_messages"] or "[]")
        summary = row["window_summary"] if row["window_summary"] is not None else (row["summary"] or "")
        return (
            AgentContextState(
                summary,
                messages_from_dict(recent),
                context_tokens=int(row["context_tokens"] or 0),
            ),
            int(row["turn_index"]),
        )

    def save_context(
        self,
        session,
        state: AgentContextState,
        turn_index: int,
    ) -> None:
        """Update full compact Session context, including the derived summary."""
        conn = self._require_transaction()
        recent = json.dumps(messages_to_dict(state.recent_messages), ensure_ascii=False, default=str)
        cur = conn.execute(
            """
            UPDATE sessions SET summary = ?, recent_messages = ?, context_tokens = ?,
                turn_index = ?, version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE workspace_id = ? AND session_id = ?
            """,
            (
                state.summary,
                recent,
                state.context_tokens,
                turn_index,
                str(session.workspace.workspace_id),
                str(session.session_id),
            ),
        )
        if cur.rowcount != 1:
            raise RuntimeError("Session context update did not affect exactly one row.")
        self._ensure_root_context_window(conn, session, state.summary, turn_index)
        self._update_uncompacted_root_summary(conn, session, state.summary)

    def save_fast_context_values(
        self,
        session,
        state: AgentContextState,
        turn_index: int,
    ) -> None:
        """Update recent context without overwriting a newer background summary."""
        conn = self._require_transaction()
        recent = json.dumps(messages_to_dict(state.recent_messages), ensure_ascii=False, default=str)
        cur = conn.execute(
            """
            UPDATE sessions SET recent_messages=?, context_tokens=?, turn_index=?,
                version=version + 1, updated_at=CURRENT_TIMESTAMP
            WHERE workspace_id=? AND session_id=?
            """,
            (
                recent,
                state.context_tokens,
                turn_index,
                str(session.workspace.workspace_id),
                str(session.session_id),
            ),
        )
        if cur.rowcount != 1:
            raise RuntimeError("Fast Session context update did not affect exactly one row.")

    def save_fast_context(self, completed: CompletedTurn) -> None:
        """Update compact context inside the active Unit of Work transaction."""
        self.save_fast_context_values(
            completed.session,
            completed.state,
            completed.turn_index,
        )

    def _ensure_root_context_window(self, conn, session, summary: str, turn_index: int) -> None:
        """Keep legacy full-context saves readable through the window model."""
        row = conn.execute(
            """
            SELECT active_context_window_id, active_branch_id FROM sessions
            WHERE workspace_id=? AND session_id=?
            """,
            (str(session.workspace.workspace_id), str(session.session_id)),
        ).fetchone()
        if not row or row["active_context_window_id"]:
            return
        window_id = f"root-{session.session_id}"
        conn.execute(
            """
            INSERT OR IGNORE INTO context_windows(
                window_id, workspace_id, session_id, branch_id, first_window_id,
                previous_window_id, summary_text, summary_through_turn,
                compacted_from_turn, compacted_through_turn, opened_at_turn,
                source_message_count
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, 0, 0, 0, ?, 0)
            """,
            (
                window_id,
                str(session.workspace.workspace_id),
                str(session.session_id),
                row["active_branch_id"],
                window_id,
                summary or "",
                turn_index,
            ),
        )
        conn.execute(
            """
            UPDATE sessions SET active_context_window_id=?
            WHERE workspace_id=? AND session_id=? AND active_context_window_id IS NULL
            """,
            (window_id, str(session.workspace.workspace_id), str(session.session_id)),
        )


    def _update_uncompacted_root_summary(self, conn, session, summary: str) -> None:
        """Mirror legacy full saves into the initial immutable-window seed."""
        conn.execute(
            """
            UPDATE context_windows
            SET summary_text=?
            WHERE window_id = (
                SELECT active_context_window_id FROM sessions
                WHERE workspace_id=? AND session_id=?
            )
              AND previous_window_id IS NULL
              AND summary_through_turn = 0
              AND source_message_count = 0
            """,
            (summary or "", str(session.workspace.workspace_id), str(session.session_id)),
        )

    def _require_transaction(self):
        if self._transaction_conn is None:
            raise RuntimeError("Session update requires an active Unit of Work.")
        return self._transaction_conn
