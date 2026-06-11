"""Workspace and session identity repository."""

from uuid import UUID, uuid4

from src.core.database.queries import SELECT_OR_CREATE_SESSION, UPSERT_WORKSPACE
from src.core.workspace.models import SessionContext, WorkspaceContext
from src.core.workspace.resolver import canonical_path_key, canonicalize_workspace


class WorkspaceRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    def resolve(self, path: str) -> WorkspaceContext:
        root = canonicalize_workspace(path)
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(UPSERT_WORKSPACE, (uuid4(), canonical_path_key(root), str(root)))
                workspace_id = cur.fetchone()[0]
            conn.commit()
        return WorkspaceContext(workspace_id=UUID(str(workspace_id)), root=root)

    def resolve_session(self, workspace: WorkspaceContext, session_name: str) -> tuple[SessionContext, bool]:
        normalized = session_name.strip()
        if not normalized:
            raise ValueError("session_name must not be empty")
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    SELECT_OR_CREATE_SESSION,
                    (uuid4(), workspace.workspace_id, normalized),
                )
                session_id, _summary, _recent, turn_index, _is_new_hint = cur.fetchone()
            conn.commit()
        session = SessionContext(UUID(str(session_id)), normalized, workspace)
        return session, int(turn_index) == 0
