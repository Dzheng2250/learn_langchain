"""Stable Workspace identity capabilities required by application services."""

from typing import Protocol

from src.core.workspace.models import SessionContext, WorkspaceContext


class WorkspaceRuntimeProvider(Protocol):
    """Return the cached runtime bound to one Workspace identity."""

    def get(self, workspace: WorkspaceContext): ...


class WorkspaceIdentityRepository(Protocol):
    """Resolve request identity without exposing a specific database backend."""

    def resolve(self, path: str) -> WorkspaceContext: ...

    def resolve_session(
        self,
        workspace: WorkspaceContext,
        session_name: str,
    ) -> tuple[SessionContext, bool]: ...

    def get_session(self, workspace_id: str, session_id: str) -> SessionContext: ...

class WorkspaceSessionLifecycleRepository(WorkspaceIdentityRepository, Protocol):
    """Workspace persistence capabilities required by the SQLite Session adapter."""

    def get_session_by_name(
        self,
        workspace: WorkspaceContext,
        session_name: str,
        *,
        include_archived: bool = False,
    ) -> tuple[SessionContext, bool] | None: ...

    def checkpoint_threads_for_session(self, session: SessionContext) -> list[str]: ...

    def archive_session(self, session: SessionContext) -> bool: ...

    def delete_session(self, session: SessionContext) -> bool: ...
