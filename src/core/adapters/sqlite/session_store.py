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
                SELECT summary, recent_messages, context_tokens, turn_index FROM sessions
                WHERE workspace_id = ? AND session_id = ?
                """,
                (str(session.workspace.workspace_id), str(session.session_id)),
            ).fetchone()
        if not row:
            raise RuntimeError("Resolved session disappeared before it could be loaded.")
        recent = json.loads(row["recent_messages"] or "[]")
        return (
            AgentContextState(
                row["summary"] or "",
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

    def _require_transaction(self):
        if self._transaction_conn is None:
            raise RuntimeError("Session update requires an active Unit of Work.")
        return self._transaction_conn
