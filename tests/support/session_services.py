"""Factories for explicitly assembled application services in tests."""

from src.core.adapters.sqlite import (
    SQLiteConversationHistoryStore,
    SQLiteSessionStore,
)
from src.core.adapters.sqlite.session_lifecycle import SQLiteSessionLifecycleStore
from src.core.agent.locking import SessionLockRegistry
from src.core.session import SessionLifecycleService
from src.core.session.checkpoint_cleanup import SessionCheckpointCleanupQueue
from src.core.session.status import SessionStatusReader


def build_session_lifecycle_service(
    *,
    database,
    workspace_repository,
    execution_repository,
    maintenance_repository=None,
    maintenance_scheduler=None,
    checkpoint_manager=None,
    lock_registry=None,
) -> SessionLifecycleService:
    """Assemble Session lifecycle collaborators without using the process container."""
    lifecycle_store = SQLiteSessionLifecycleStore(
        workspace_repository=workspace_repository,
        history_store=SQLiteConversationHistoryStore(database),
    )
    return SessionLifecycleService(
        lifecycle_store=lifecycle_store,
        lock_registry=lock_registry or SessionLockRegistry(),
        execution_repository=execution_repository,
        checkpoint_manager=checkpoint_manager,
        checkpoint_cleanup=SessionCheckpointCleanupQueue(
            maintenance_repository,
            maintenance_scheduler,
        ),
        status_reader=SessionStatusReader(
            lifecycle_store=lifecycle_store,
            session_store=SQLiteSessionStore(database),
            execution_repository=execution_repository,
            maintenance_repository=maintenance_repository,
        ),
    )
