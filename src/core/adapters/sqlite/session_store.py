"""SQLite Session context adapter."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from langchain_core.messages import messages_from_dict

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
        write_delegate=None,
    ) -> None:
        self.database = database
        self._transaction_conn = transaction_conn
        self._write_delegate = write_delegate

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

    def save_fast_context(self, completed: CompletedTurn) -> None:
        """Update compact context through the existing transaction-safe path."""
        if self._transaction_conn is None or self._write_delegate is None:
            raise RuntimeError("Session fast update requires an active Unit of Work.")
        self._write_delegate.save_fast_session_in_transaction(
            self._transaction_conn,
            completed.session,
            completed.state,
            completed.turn_index,
        )
