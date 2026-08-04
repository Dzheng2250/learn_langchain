"""Shared SQLite queries for the active conversation branch lineage."""

from __future__ import annotations


def active_branch_head(conn, *, workspace_id: str, session_id: str):
    """Return whether branch metadata exists and its trustworthy head."""
    session_row = conn.execute(
        """
        SELECT active_branch_id FROM sessions
        WHERE workspace_id=? AND session_id=?
        """,
        (workspace_id, session_id),
    ).fetchone()
    branch_id = session_row["active_branch_id"] if session_row else None
    if not branch_id:
        branch_exists = conn.execute(
            """
            SELECT EXISTS(
                SELECT 1 FROM branches
                WHERE workspace_id=? AND session_id=?
            ) AS present
            """,
            (workspace_id, session_id),
        ).fetchone()
        return bool(branch_exists and branch_exists["present"]), None
    branch = conn.execute(
        """
        SELECT b.head_message_id,
               EXISTS(
                   SELECT 1 FROM messages AS m
                   WHERE m.message_id=b.head_message_id
                     AND m.workspace_id=b.workspace_id
                     AND m.session_id=b.session_id
               ) AS head_valid
        FROM branches AS b
        WHERE b.branch_id=? AND b.workspace_id=? AND b.session_id=?
        """,
        (branch_id, workspace_id, session_id),
    ).fetchone()
    if branch and bool(branch["head_valid"]):
        return True, branch["head_message_id"]
    return True, None


def active_lineage_rows(
    conn,
    *,
    workspace_id: str,
    session_id: str,
    after_turn: int,
    through_turn: int | None = None,
):
    """Return ordered messages in the active lineage and requested Turn range."""
    branch_managed, head_message_id = active_branch_head(
        conn,
        workspace_id=workspace_id,
        session_id=session_id,
    )
    if head_message_id:
        return conn.execute(
            """
            WITH RECURSIVE lineage AS (
                SELECT message_id, parent_message_id, turn_index, message_ordinal, raw
                FROM messages
                WHERE message_id=? AND workspace_id=? AND session_id=?
                UNION
                SELECT parent.message_id, parent.parent_message_id,
                       parent.turn_index, parent.message_ordinal, parent.raw
                FROM messages AS parent
                JOIN lineage AS child ON parent.message_id=child.parent_message_id
                WHERE parent.workspace_id=? AND parent.session_id=?
            )
            SELECT message_id, turn_index, message_ordinal, raw FROM lineage
            WHERE turn_index>? AND (? IS NULL OR turn_index<=?)
            ORDER BY turn_index, message_ordinal
            """,
            (
                head_message_id,
                workspace_id,
                session_id,
                workspace_id,
                session_id,
                int(after_turn),
                through_turn,
                through_turn,
            ),
        ).fetchall()
    if branch_managed:
        return []
    return conn.execute(
        """
        SELECT message_id, turn_index, message_ordinal, raw FROM messages
        WHERE workspace_id=? AND session_id=? AND turn_index>?
          AND (? IS NULL OR turn_index<=?)
        ORDER BY turn_index, message_ordinal
        """,
        (
            workspace_id,
            session_id,
            int(after_turn),
            through_turn,
            through_turn,
        ),
    ).fetchall()
