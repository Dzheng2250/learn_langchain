"""Workspace and Session identity backed by authoritative local SQLite state."""

from uuid import UUID, uuid4

from src.core.state.database import LocalStateDatabase
from src.core.workspace.models import SessionContext, WorkspaceContext
from src.core.workspace.resolver import canonical_path_key, canonicalize_workspace


class LocalWorkspaceRepository:
    """Resolve stable Workspace and Session identities without PostgreSQL."""

    def __init__(self, database: LocalStateDatabase) -> None:
        self.database = database

    def resolve(self, path: str) -> WorkspaceContext:
        """Atomically register or return one canonical Workspace."""
        root = canonicalize_workspace(path)
        canonical = canonical_path_key(root)
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT workspace_id FROM workspaces WHERE canonical_path = ?",
                (canonical,),
            ).fetchone()
            if row:
                workspace_id = row["workspace_id"]
                conn.execute(
                    "UPDATE workspaces SET display_path = ?, updated_at = CURRENT_TIMESTAMP WHERE workspace_id = ?",
                    (str(root), workspace_id),
                )
            else:
                workspace_id = str(uuid4())
                conn.execute(
                    """
                    INSERT INTO workspaces(workspace_id, canonical_path, display_path)
                    VALUES (?, ?, ?)
                    """,
                    (workspace_id, canonical, str(root)),
                )
        return WorkspaceContext(UUID(workspace_id), root)

    def resolve_session(self, workspace: WorkspaceContext, session_name: str) -> tuple[SessionContext, bool]:
        """Resolve one Workspace-local Session and create its main branch."""
        normalized = session_name.strip()
        if not normalized:
            raise ValueError("session_name must not be empty")
        with self.database.transaction() as conn:
            row = conn.execute(
                """
                SELECT session_id, turn_index, archived_at FROM sessions
                WHERE workspace_id = ? AND session_name = ?
                """,
                (str(workspace.workspace_id), normalized),
            ).fetchone()
            if row:
                if row["archived_at"]:
                    raise RuntimeError(
                        f"Session '{normalized}' is archived. Use a different session name "
                        "or hard-delete it before recreating it."
                    )
                session_id = row["session_id"]
                turn_index = int(row["turn_index"])
            else:
                session_id = str(uuid4())
                branch_id = str(uuid4())
                window_id = f"root-{session_id}"
                conn.execute(
                    """
                    INSERT INTO sessions(
                        session_id, workspace_id, session_name, active_branch_id,
                        active_context_window_id
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (session_id, str(workspace.workspace_id), normalized, branch_id, window_id),
                )
                conn.execute(
                    """
                    INSERT INTO branches(branch_id, workspace_id, session_id, branch_name)
                    VALUES (?, ?, ?, 'main')
                    """,
                    (branch_id, str(workspace.workspace_id), session_id),
                )
                conn.execute(
                    """
                    INSERT INTO context_windows(
                        window_id, workspace_id, session_id, branch_id, first_window_id,
                        previous_window_id, summary_text, summary_through_turn,
                        compacted_from_turn, compacted_through_turn, opened_at_turn,
                        source_message_count
                    ) VALUES (?, ?, ?, ?, ?, NULL, '', 0, 0, 0, 0, 0)
                    """,
                    (window_id, str(workspace.workspace_id), session_id, branch_id, window_id),
                )
                turn_index = 0
        return SessionContext(UUID(session_id), normalized, workspace), turn_index == 0

    def get_session_by_name(
        self,
        workspace: WorkspaceContext,
        session_name: str,
        *,
        include_archived: bool = False,
    ) -> tuple[SessionContext, bool] | None:
        """Return an existing Workspace-local Session without creating it."""
        normalized = session_name.strip()
        if not normalized:
            raise ValueError("session_name must not be empty")
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT session_id, archived_at FROM sessions
                WHERE workspace_id = ? AND session_name = ?
                """,
                (str(workspace.workspace_id), normalized),
            ).fetchone()
        if not row:
            return None
        archived = bool(row["archived_at"])
        if archived and not include_archived:
            return None
        return SessionContext(UUID(row["session_id"]), normalized, workspace), archived

    def archive_session(self, session: SessionContext) -> bool:
        """Mark a Session unavailable while retaining its audit history."""
        with self.database.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE sessions
                SET archived_at = COALESCE(archived_at, CURRENT_TIMESTAMP),
                    pending_execution_id = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE workspace_id = ? AND session_id = ?
                  AND archived_at IS NULL
                """,
                (str(session.workspace.workspace_id), str(session.session_id)),
            )
        return cur.rowcount == 1

    def delete_session(self, session: SessionContext) -> bool:
        """Permanently delete a Session and rows covered by local foreign keys."""
        with self.database.transaction() as conn:
            # Migration-created databases may predate the composite Session FK.
            # Remove Session-scoped grants explicitly; Workspace rules survive.
            conn.execute(
                """
                DELETE FROM tool_approval_audit
                WHERE workspace_id = ? AND session_id = ?
                """,
                (str(session.workspace.workspace_id), str(session.session_id)),
            )
            conn.execute(
                """
                DELETE FROM tool_approval_requests
                WHERE workspace_id = ? AND sessi on_id = ?
                """,
                (str(session.workspace.workspace_id), str(session.session_id)),
            )
            conn.execute(
                """
                DELETE FROM tool_permission_rules
                WHERE workspace_id = ? AND session_id = ?
                """,
                (str(session.workspace.workspace_id), str(session.session_id)),
            )
            cur = conn.execute(
                """
                DELETE FROM sessions
                WHERE workspace_id = ? AND session_id = ?
                """,
                (str(session.workspace.workspace_id), str(session.session_id)),
            )
        return cur.rowcount == 1

    def checkpoint_threads_for_session(self, session: SessionContext) -> list[str]:
        """Return checkpoint thread IDs owned by one Session."""
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT checkpoint_thread_id FROM executions
                WHERE workspace_id = ? AND session_id = ?
                """,
                (str(session.workspace.workspace_id), str(session.session_id)),
            ).fetchall()
        return [row["checkpoint_thread_id"] for row in rows]

    def pending_execution(self, session: SessionContext):
        """Return the Session's recoverable execution, if one exists."""
        with self.database.connect() as conn:
            return conn.execute(
                """
                SELECT e.* FROM sessions s
                JOIN executions e ON e.execution_id = s.pending_execution_id
                WHERE s.workspace_id = ? AND s.session_id = ?
                """,
                (str(session.workspace.workspace_id), str(session.session_id)),
            ).fetchone()

    def get_session(self, workspace_id: str, session_id: str) -> SessionContext:
        """Rehydrate immutable Session identity for a background job."""
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT w.workspace_id, w.display_path, s.session_id, s.session_name
                FROM sessions s
                JOIN workspaces w ON w.workspace_id=s.workspace_id
                WHERE s.workspace_id=? AND s.session_id=?
                """,
                (workspace_id, session_id),
            ).fetchone()
        if not row:
            raise RuntimeError("Maintenance job refers to a missing Session.")
        from pathlib import Path

        return SessionContext(
            UUID(row["session_id"]),
            row["session_name"],
            WorkspaceContext(UUID(row["workspace_id"]), Path(row["display_path"]).resolve()),
        )
