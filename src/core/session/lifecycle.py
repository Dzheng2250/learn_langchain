"""Session status, recovery, archive, and deletion operations.

This module keeps Session control paths out of the foreground Agent turn
runner. These operations mutate or inspect durable Session state, but they do
not call the model or stream LangGraph events.
"""

from collections.abc import Callable
from threading import RLock
from uuid import UUID

from src.core.session.checkpoint_cleanup import SessionCheckpointCleanupQueue
from src.core.session.responses import (
    active_status_response,
    archive_response,
    archived_discard_response,
    archived_reset_response,
    archived_status_response,
    hard_delete_response,
    idle_discard_response,
    not_found_delete_response,
    reset_response,
)
from src.core.state.contracts import StateStore
from src.core.workspace.contracts import WorkspaceIdentityRepository
from src.core.workspace.models import SessionContext


class SessionLifecycleService:
    """Manage Session lifecycle operations outside Agent execution.

    The service depends on repositories and stores through constructor
    injection. It intentionally does not create database connections or model
    providers by itself; the Core DI container decides concrete implementations.
    """

    def __init__(
        self,
        *,
        workspace_repository: WorkspaceIdentityRepository,
        state_store_factory: Callable[[], StateStore],
        lock_registry,
        execution_repository=None,
        checkpoint_manager=None,
        maintenance_repository=None,
        maintenance_scheduler=None,
    ) -> None:
        self.workspace_repository = workspace_repository
        self.state_store_factory = state_store_factory
        self.lock_registry = lock_registry
        self.execution_repository = execution_repository
        self.checkpoint_manager = checkpoint_manager
        self.maintenance_repository = maintenance_repository
        self.checkpoint_cleanup = SessionCheckpointCleanupQueue(
            maintenance_repository,
            maintenance_scheduler,
        )

    def session_status(self, workspace_root: str, session_name: str) -> dict:
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
        pending = self.execution_repository.get_attached(session) if self.execution_repository else None
        maintenance = (
            self.maintenance_repository.counts_for_session(
                str(workspace.workspace_id),
                str(session.session_id),
            )
            if self.maintenance_repository is not None
            else {"pending": 0, "running": 0, "failed": 0}
        )
        if self.maintenance_repository is not None:
            maintenance["recent_failures"] = (
                self.maintenance_repository.recent_failures_for_session(
                    str(workspace.workspace_id),
                    str(session.session_id),
                )
            )
        else:
            maintenance["recent_failures"] = []
        return active_status_response(
            workspace,
            session,
            context_state,
            pending,
            maintenance,
        )

    def discard_pending(self, workspace_root: str, session_name: str) -> dict:
        """Discard the pending execution while retaining its audit rows."""
        if self.execution_repository is None:
            raise RuntimeError("Resumable execution is not configured.")
        workspace = self.workspace_repository.resolve(workspace_root)
        existing = self._get_existing_session_by_name(workspace, session_name)
        if existing is not None and existing[1]:
            return archived_discard_response(existing[0])
        session, _ = self.workspace_repository.resolve_session(workspace, session_name)
        with self._lock_for(session.session_id):
            if self.execution_repository.get_attached(session) is None:
                return idle_discard_response(session)
            pending = self.execution_repository.discard(session)
            self.checkpoint_cleanup.enqueue(session, pending)
        return {"status": "discarded", "execution_id": pending.execution_id}

    def delete_session(
        self,
        workspace_root: str,
        session_name: str,
        *,
        hard_delete: bool = False,
    ) -> dict:
        """Archive by default, or permanently delete one Workspace-local Session."""
        workspace = self.workspace_repository.resolve(workspace_root)
        found = self._get_existing_session_by_name(workspace, session_name)
        if found is None:
            return not_found_delete_response(
                workspace,
                session_name,
                hard_delete=hard_delete,
            )
        session, archived = found
        with self._lock_for(session.session_id):
            if hard_delete:
                checkpoint_threads = self.workspace_repository.checkpoint_threads_for_session(
                    session
                )
                if self.checkpoint_manager is not None:
                    for thread_id in checkpoint_threads:
                        try:
                            self.checkpoint_manager.delete_thread(thread_id)
                        except Exception:
                            # State deletion remains authoritative even when a
                            # checkpoint row was already missing.
                            pass
                deleted = self.workspace_repository.delete_session(session)
                return hard_delete_response(
                    session,
                    deleted=deleted,
                    checkpoint_threads_deleted=len(checkpoint_threads),
                )

            cleanup_enqueued = False
            if not archived and self.execution_repository is not None:
                pending = self.execution_repository.get_attached(session)
                if pending is not None:
                    self.execution_repository.discard(session)
                    cleanup_enqueued = self.checkpoint_cleanup.enqueue(session, pending)
            archived_now = (
                False if archived else self.workspace_repository.archive_session(session)
            )
            return archive_response(
                session,
                archived_now=archived_now,
                cleanup_enqueued=cleanup_enqueued,
            )

    def reset_session(self, workspace_root: str, session_name: str) -> dict:
        """Rebuild recent_messages from archived messages and reset context_tokens."""
        workspace = self.workspace_repository.resolve(workspace_root)
        found = self._get_existing_session_by_name(workspace, session_name)
        if found is not None and found[1]:
            return archived_reset_response(workspace, found[0])
        session, _ = self.workspace_repository.resolve_session(workspace, session_name)
        with self._lock_for(session.session_id):
            store = self.state_store_factory()
            try:
                count = store.rebuild_recent_messages_from_archive(session)
            finally:
                store.close()
        return reset_response(session, count)

    def _get_existing_session_by_name(
        self,
        workspace,
        session_name: str,
    ) -> tuple[SessionContext, bool] | None:
        """Return an existing Session when lookup-only access is supported."""
        getter = getattr(self.workspace_repository, "get_session_by_name", None)
        if getter is None:
            return None
        return getter(workspace, session_name, include_archived=True)

    def _lock_for(self, session_id: UUID) -> RLock:
        """Return the shared Session lock from the injected registry."""
        return self.lock_registry.get(session_id)
