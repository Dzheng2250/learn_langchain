"""Adapter from generic Workspace repositories to the Agent Session port."""

from collections.abc import Callable

from src.core.workspace.contracts import WorkspaceIdentityRepository
from src.core.workspace.models import SessionContext, WorkspaceContext


class RepositoryAgentSessionStore:
    """Expose lookup-safe Agent Session identity over a Workspace repository."""

    def __init__(self, repository: WorkspaceIdentityRepository) -> None:
        self._repository = repository

    def resolve_workspace(self, workspace_root: str) -> WorkspaceContext:
        return self._repository.resolve(workspace_root)

    def find_session(
        self,
        workspace: WorkspaceContext,
        session_name: str,
    ) -> tuple[SessionContext, bool] | None:
        getter: Callable | None = getattr(
            self._repository,
            "get_session_by_name",
            None,
        )
        if getter is None:
            return None
        return getter(workspace, session_name, include_archived=True)

    def resolve_session(
        self,
        workspace: WorkspaceContext,
        session_name: str,
    ) -> tuple[SessionContext, bool]:
        return self._repository.resolve_session(workspace, session_name)
