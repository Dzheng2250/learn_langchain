"""SQLite projection outbox adapter."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.state.database import LocalStateDatabase


class SQLiteProjectionOutboxStore:
    """Append optional PostgreSQL projection events inside a SQLite transaction."""

    def __init__(
        self,
        database: LocalStateDatabase,
        *,
        transaction_conn=None,
        enabled: bool = True,
    ) -> None:
        self.database = database
        self._transaction_conn = transaction_conn
        self.enabled = enabled

    def enqueue(
        self,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict,
    ) -> None:
        """Append one outbox row when projection is enabled."""
        if not self.enabled:
            return
        conn = self._require_transaction()
        conn.execute(
            """
            INSERT INTO projection_outbox(event_type, aggregate_type, aggregate_id, payload)
            VALUES (?, ?, ?, ?)
            """,
            (event_type, aggregate_type, aggregate_id, json.dumps(payload, ensure_ascii=False)),
        )

    def _require_transaction(self):
        if self._transaction_conn is None:
            raise RuntimeError("Projection outbox enqueue requires an active transaction.")
        return self._transaction_conn
