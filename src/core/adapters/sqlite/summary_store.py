"""SQLite adapter for derived conversation summaries."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from langchain_core.messages import messages_from_dict

if TYPE_CHECKING:
    from src.core.state.database import LocalStateDatabase
    from src.core.workspace.models import SessionContext


class SQLiteSummaryStore:
    """Read and update derived Session summaries without exposing SQL upstream."""

    def __init__(self, database: LocalStateDatabase) -> None:
        self.database = database

    def load_summary_source(
        self,
        session: SessionContext,
        target_turn: int,
    ) -> tuple[str, int, list[tuple[int, object]]]:
        """Load committed messages newer than the current summary watermark."""
        with self.database.connect() as conn:
            session_row = conn.execute(
                """
                SELECT summary, summary_through_turn FROM sessions
                WHERE workspace_id=? AND session_id=?
                """,
                (str(session.workspace.workspace_id), str(session.session_id)),
            ).fetchone()
            if not session_row:
                raise RuntimeError(
                    "Session disappeared before context summary maintenance."
                )
            rows = conn.execute(
                """
                SELECT turn_index, raw FROM messages
                WHERE workspace_id=? AND session_id=?
                  AND turn_index > ? AND turn_index <= ?
                ORDER BY turn_index, created_at, message_id
                """,
                (
                    str(session.workspace.workspace_id),
                    str(session.session_id),
                    int(session_row["summary_through_turn"]),
                    target_turn,
                ),
            ).fetchall()
        return (
            session_row["summary"] or "",
            int(session_row["summary_through_turn"]),
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
        """Write a summary only when the stored watermark still matches."""
        with self.database.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE sessions
                SET summary=?, summary_through_turn=?, version=version + 1,
                    updated_at=CURRENT_TIMESTAMP
                WHERE workspace_id=? AND session_id=? AND summary_through_turn=?
                """,
                (
                    summary,
                    summary_through_turn,
                    str(session.workspace.workspace_id),
                    str(session.session_id),
                    expected_summary_through_turn,
                ),
            )
        return cur.rowcount == 1
