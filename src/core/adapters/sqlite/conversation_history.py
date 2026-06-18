"""SQLite conversation history adapter."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from langchain_core.messages import messages_from_dict, messages_to_dict

from src.config.settings import RECENT_MESSAGE_LIMIT

if TYPE_CHECKING:
    from src.core.finalization.models import CompletedTurn
    from src.core.state.database import LocalStateDatabase


class SQLiteConversationHistoryStore:
    """Read conversation history and optionally append inside a Unit of Work."""

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

    def append_turn(self, completed: CompletedTurn) -> list[str]:
        """Append messages through the existing transaction-safe write path."""
        if self._transaction_conn is None or self._write_delegate is None:
            raise RuntimeError("Conversation append requires an active Unit of Work.")
        return self._write_delegate.append_messages_in_transaction(
            self._transaction_conn,
            completed.session,
            completed.turn_index,
            completed.messages,
            execution_id=completed.execution_id,
        )

    def load_turn(self, session, turn_index: int) -> tuple[list, list[str]]:
        """Load one committed Turn while preserving message order."""
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT message_id, raw FROM messages
                WHERE workspace_id=? AND session_id=? AND turn_index=?
                ORDER BY rowid
                """,
                (
                    str(session.workspace.workspace_id),
                    str(session.session_id),
                    turn_index,
                ),
            ).fetchall()
        raw = [json.loads(row["raw"]) for row in rows]
        return messages_from_dict(raw), [row["message_id"] for row in rows]

    def rebuild_recent(self, session) -> int:
        """Rebuild compact recent context from durable message history."""
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT raw FROM messages
                WHERE workspace_id=? AND session_id=?
                ORDER BY rowid DESC
                LIMIT ?
                """,
                (
                    str(session.workspace.workspace_id),
                    str(session.session_id),
                    RECENT_MESSAGE_LIMIT,
                ),
            ).fetchall()
        recovered = messages_from_dict(
            [json.loads(row["raw"]) for row in reversed(rows)]
        )
        with self.database.transaction() as conn:
            recent = json.dumps(
                messages_to_dict(recovered), ensure_ascii=False, default=str
            )
            conn.execute(
                """
                UPDATE sessions SET recent_messages=?, context_tokens=0,
                    version=version + 1, updated_at=CURRENT_TIMESTAMP
                WHERE workspace_id=? AND session_id=?
                """,
                (
                    recent,
                    str(session.workspace.workspace_id),
                    str(session.session_id),
                ),
            )
        return len(recovered)
