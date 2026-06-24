"""Session identity and lifecycle persistence ports."""

from typing import Protocol

from src.core.workspace.models import SessionContext, WorkspaceContext


class AgentSessionStore(Protocol):
    """Resolve Session identity required by foreground Agent requests."""

    def resolve_workspace(self, workspace_root: str) -> WorkspaceContext:
        """Resolve or register the Workspace owning the Session."""

    def find_session(
        self,
        workspace: WorkspaceContext,
        session_name: str,
    ) -> tuple[SessionContext, bool] | None:
        """Return an existing Session and whether it is archived."""

    def resolve_session(
        self,
        workspace: WorkspaceContext,
        session_name: str,
    ) -> tuple[SessionContext, bool]:
        """Resolve or create an active Session."""


class SessionLifecycleStore(AgentSessionStore, Protocol):
    """Mutate Session lifecycle without exposing its storage backend."""

    def checkpoint_threads(self, session: SessionContext) -> list[str]:
        """Return checkpoint thread identifiers owned by the Session."""

    def archive(self, session: SessionContext) -> bool:
        """Archive the Session while preserving its durable history."""

    def delete(self, session: SessionContext) -> bool:
        """Permanently delete the Session and backend-owned dependent rows."""

    def rebuild_recent(self, session: SessionContext) -> int:
        """Rebuild compact recent context from authoritative history."""
