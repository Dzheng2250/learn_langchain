"""Session status, recovery, archive, and deletion operations.

This module keeps Session control paths out of the foreground Agent turn
runner. These operations mutate or inspect durable Session state, but they do
not call the model or stream LangGraph events.
"""

from threading import RLock
from uuid import UUID

from src.core.ports.session import SessionLifecycleStore
from src.core.session.checkpoint_cleanup import SessionCheckpointCleanupQueue
from src.core.session.responses import (
    archive_response,
    archived_discard_response,
    archived_reset_response,
    hard_delete_response,
    idle_discard_response,
    not_found_delete_response,
    reset_response,
)
from src.core.session.status import SessionStatusReader


class SessionLifecycleService:
    """Coordinate Session lifecycle rules through injected application ports."""

    def __init__(
        self,
        *,
        lifecycle_store: SessionLifecycleStore,
        lock_registry,
        execution_repository,
        checkpoint_manager,
        checkpoint_cleanup: SessionCheckpointCleanupQueue,
        status_reader: SessionStatusReader,
    ) -> None:
        self.lifecycle_store = lifecycle_store
        self.lock_registry = lock_registry
        self.execution_repository = execution_repository
        self.checkpoint_manager = checkpoint_manager
        self.checkpoint_cleanup = checkpoint_cleanup
        self.status_reader = status_reader

    def session_status(self, workspace_root: str, session_name: str) -> dict:
        """Return compact pending-execution state without running the graph."""
        return self.status_reader.get(workspace_root, session_name)

    def discard_pending(self, workspace_root: str, session_name: str) -> dict:
        """Discard the pending execution while retaining its audit rows."""
        workspace = self.lifecycle_store.resolve_workspace(workspace_root)
        existing = self.lifecycle_store.find_session(workspace, session_name)
        if existing is not None and existing[1]:
            return archived_discard_response(existing[0])
        session, _ = self.lifecycle_store.resolve_session(workspace, session_name)
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
        workspace = self.lifecycle_store.resolve_workspace(workspace_root)
        found = self.lifecycle_store.find_session(workspace, session_name)
        if found is None:
            return not_found_delete_response(
                workspace,
                session_name,
                hard_delete=hard_delete,
            )
        session, archived = found
        with self._lock_for(session.session_id):
            if hard_delete:
                checkpoint_threads = self.lifecycle_store.checkpoint_threads(session)
                if self.checkpoint_manager is not None:
                    for thread_id in checkpoint_threads:
                        try:
                            self.checkpoint_manager.delete_thread(thread_id)
                        except Exception:
                            # State deletion remains authoritative when a checkpoint
                            # row was already missing or cannot be cleaned immediately.
                            pass
                deleted = self.lifecycle_store.delete(session)
                return hard_delete_response(
                    session,
                    deleted=deleted,
                    checkpoint_threads_deleted=len(checkpoint_threads),
                )

            cleanup_enqueued = False
            if not archived:
                pending = self.execution_repository.get_attached(session)
                if pending is not None:
                    self.execution_repository.discard(session)
                    cleanup_enqueued = self.checkpoint_cleanup.enqueue(session, pending)
            archived_now = False if archived else self.lifecycle_store.archive(session)
            return archive_response(
                session,
                archived_now=archived_now,
                cleanup_enqueued=cleanup_enqueued,
            )

    def reset_session(self, workspace_root: str, session_name: str) -> dict:
        """Rebuild recent_messages from archived messages and reset context_tokens."""
        workspace = self.lifecycle_store.resolve_workspace(workspace_root)
        found = self.lifecycle_store.find_session(workspace, session_name)
        if found is not None and found[1]:
            return archived_reset_response(workspace, found[0])
        session, _ = self.lifecycle_store.resolve_session(workspace, session_name)
        with self._lock_for(session.session_id):
            count = self.lifecycle_store.rebuild_recent(session)
        return reset_response(session, count)

    def _lock_for(self, session_id: UUID) -> RLock:
        """Return the shared Session lock from the injected registry."""
        return self.lock_registry.get(session_id)
