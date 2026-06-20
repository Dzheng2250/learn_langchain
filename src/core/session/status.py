"""Read-only Session status aggregation."""

from collections.abc import Callable

from src.core.session.responses import active_status_response, archived_status_response
from src.core.state.contracts import StateStore
from src.core.workspace.contracts import WorkspaceIdentityRepository


class SessionStatusReader:
    """Build user-facing Session status without mutating Session state."""

    def __init__(
        self,
        *,
        workspace_repository: WorkspaceIdentityRepository,
        state_store_factory: Callable[[], StateStore],
        execution_repository=None,
        maintenance_repository=None,
    ) -> None:
        self.workspace_repository = workspace_repository
        self.state_store_factory = state_store_factory
        self.execution_repository = execution_repository
        self.maintenance_repository = maintenance_repository

    def get(self, workspace_root: str, session_name: str) -> dict:
        """Return compact pending-execution state without running the graph."""
        workspace = self.workspace_repository.resolve(workspace_root)
        existing = self._get_existing_session_by_name(workspace, session_name)
        if existing is not None and existing[1]:
            return archived_status_response(workspace, existing[0])
        session, _ = self.workspace_repository.resolve_session(workspace, session_name)
        store = self.state_store_factory()
        try:
            context_state, _ = store.load_session(session)
        except Exception:
            context_state = None
        finally:
            store.close()
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

    def _get_existing_session_by_name(self, workspace, session_name: str):
        """Return an existing Session when lookup-only access is supported."""
        getter = getattr(self.workspace_repository, "get_session_by_name", None)
        if getter is None:
            return None
        return getter(workspace, session_name, include_archived=True)
