"""Read-only Session status aggregation."""

from src.core.ports.session import SessionLifecycleStore
from src.core.ports.state import SessionStore
from src.core.session.responses import active_status_response, archived_status_response


class SessionStatusReader:
    """Build user-facing Session status through backend-neutral read ports."""

    def __init__(
        self,
        *,
        lifecycle_store: SessionLifecycleStore,
        session_store: SessionStore,
        execution_repository=None,
        maintenance_repository=None,
    ) -> None:
        self.lifecycle_store = lifecycle_store
        self.session_store = session_store
        self.execution_repository = execution_repository
        self.maintenance_repository = maintenance_repository

    def get(self, workspace_root: str, session_name: str) -> dict:
        """Return compact pending-execution state without running the graph."""
        workspace = self.lifecycle_store.resolve_workspace(workspace_root)
        existing = self.lifecycle_store.find_session(workspace, session_name)
        if existing is not None and existing[1]:
            return archived_status_response(workspace, existing[0])
        session, _ = self.lifecycle_store.resolve_session(workspace, session_name)
        try:
            context_state, _ = self.session_store.load_context(session)
        except Exception:
            context_state = None
        pending = (
            self.execution_repository.get_attached(session)
            if self.execution_repository
            else None
        )
        maintenance = self._maintenance_status(workspace, session)
        return active_status_response(
            workspace,
            session,
            context_state,
            pending,
            maintenance,
        )

    def _maintenance_status(self, workspace, session) -> dict:
        if self.maintenance_repository is None:
            return {"pending": 0, "running": 0, "failed": 0, "recent_failures": []}
        maintenance = self.maintenance_repository.counts_for_session(
            str(workspace.workspace_id),
            str(session.session_id),
        )
        maintenance["recent_failures"] = (
            self.maintenance_repository.recent_failures_for_session(
                str(workspace.workspace_id),
                str(session.session_id),
            )
        )
        return maintenance
