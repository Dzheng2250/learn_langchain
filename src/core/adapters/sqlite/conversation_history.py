"""SQLite conversation history adapter."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import uuid4

from langchain_core.messages import messages_from_dict, messages_to_dict

from src.config.settings import RECENT_MESSAGE_LIMIT
from src.core.common.content import message_content_text
from src.core.telemetry import emit_event

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
    ) -> None:
        self.database = database
        self._transaction_conn = transaction_conn

    def append_turn(self, completed: CompletedTurn) -> list[str]:
        """Append all messages from one completed Turn inside an active UoW."""
        if self._transaction_conn is None:
            raise RuntimeError("Conversation append requires an active Unit of Work.")
        return self.append_messages(
            completed.session,
            completed.turn_index,
            completed.messages,
            execution_id=completed.execution_id,
        )

    def append_messages(
        self,
        session,
        turn_index: int,
        messages: list,
        *,
        execution_id: str | None = None,
    ) -> list[str]:
        """Append messages and advance the active branch head in one transaction."""
        conn = self._require_transaction()
        session_row = conn.execute(
            "SELECT active_branch_id FROM sessions WHERE session_id = ?",
            (str(session.session_id),),
        ).fetchone()
        branch_id = session_row["active_branch_id"] if session_row else None
        head = None
        if branch_id:
            branch = conn.execute(
                "SELECT head_message_id FROM branches WHERE branch_id = ?",
                (branch_id,),
            ).fetchone()
            head = branch["head_message_id"] if branch else None
        ordinal_row = conn.execute(
            """
            SELECT COALESCE(MAX(message_ordinal), 0) AS max_ordinal
            FROM messages WHERE workspace_id=? AND session_id=?
            """,
            (str(session.workspace.workspace_id), str(session.session_id)),
        ).fetchone()
        next_ordinal = int(ordinal_row["max_ordinal"] or 0) + 1
        ids = []
        for message in messages:
            message_id = str(uuid4())
            raw = messages_to_dict([message])[0]
            conn.execute(
                """
                INSERT INTO messages(
                    message_id, workspace_id, session_id, branch_id, parent_message_id,
                    execution_id, role, content, message_type, raw, turn_index,
                    message_ordinal
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    str(session.workspace.workspace_id),
                    str(session.session_id),
                    branch_id,
                    head,
                    execution_id,
                    self._message_role(message),
                    self._message_content(message),
                    message.__class__.__name__,
                    json.dumps(raw, ensure_ascii=False, default=str),
                    turn_index,
                    next_ordinal,
                ),
            )
            ids.append(message_id)
            head = message_id
            next_ordinal += 1
        if branch_id and head:
            conn.execute(
                """
                UPDATE branches SET head_message_id = ?, version = version + 1,
                    updated_at = CURRENT_TIMESTAMP WHERE branch_id = ?
                """,
                (head, branch_id),
            )
        return ids

    def load_turn(self, session, turn_index: int) -> tuple[list, list[str]]:
        """Load one committed Turn while preserving message order."""
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT message_id, raw FROM messages
                WHERE workspace_id=? AND session_id=? AND turn_index=?
                ORDER BY turn_index, message_ordinal
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
                ORDER BY message_ordinal DESC
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

    def _require_transaction(self):
        if self._transaction_conn is None:
            raise RuntimeError("Conversation append requires an active Unit of Work.")
        return self._transaction_conn

    def _message_role(self, message) -> str:
        role = {
            "HumanMessage": "user",
            "AIMessage": "assistant",
            "ToolMessage": "tool",
            "SystemMessage": "system",
        }.get(message.__class__.__name__)
        if role is None:
            emit_event(
                "unknown_message_role",
                "sqlite_conversation_history",
                "Archived a message type without a known conversation role.",
                {"message_type": message.__class__.__name__},
                level="warning",
            )
            return "unknown"
        return role

    def _message_content(self, message) -> str:
        return message_content_text(message)
