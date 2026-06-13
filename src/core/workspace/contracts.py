"""Stable Workspace identity capabilities required by application services."""

from typing import Protocol

from src.core.workspace.models import SessionContext, WorkspaceContext


class WorkspaceIdentityRepository(Protocol):
    """Resolve request identity without exposing a specific database backend."""

    def resolve(self, path: str) -> WorkspaceContext: ...

    def resolve_session(
        self,
        workspace: WorkspaceContext,
        session_name: str,
    ) -> tuple[SessionContext, bool]: ...

    def get_session(self, workspace_id: str, session_id: str) -> SessionContext: ...
