"""SQLite adapter for Session identity and lifecycle persistence."""

from src.core.ports.session import SessionLifecycleStore
from src.core.ports.state import ConversationHistoryStore
from src.core.workspace.contracts import WorkspaceSessionLifecycleRepository
from src.core.workspace.models import SessionContext, WorkspaceContext


class SQLiteSessionLifecycleStore(SessionLifecycleStore):
    """Adapt SQLite Workspace and history stores to the lifecycle port."""

    def __init__(
        self,
        *,
        workspace_repository: WorkspaceSessionLifecycleRepository,
        history_store: ConversationHistoryStore,
    ) -> None:
        self._workspaces = workspace_repository
        self._history = history_store

    def resolve_workspace(self, workspace_root: str) -> WorkspaceContext:
        return self._workspaces.resolve(workspace_root)

    def find_session(
        self,
        workspace: WorkspaceContext,
        session_name: str,
    ) -> tuple[SessionContext, bool] | None:
        return self._workspaces.get_session_by_name(
            workspace,
            session_name,
            include_archived=True,
        )

    def resolve_session(
        self,
        workspace: WorkspaceContext,
        session_name: str,
    ) -> tuple[SessionContext, bool]:
        return self._workspaces.resolve_session(workspace, session_name)

    def checkpoint_threads(self, session: SessionContext) -> list[str]:
        return self._workspaces.checkpoint_threads_for_session(session)

    def archive(self, session: SessionContext) -> bool:
        return self._workspaces.archive_session(session)

    def delete(self, session: SessionContext) -> bool:
        return self._workspaces.delete_session(session)

    def rebuild_recent(self, session: SessionContext) -> int:
        return self._history.rebuild_recent(session)
