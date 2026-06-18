"""Persistence ports consumed by application services.

The interfaces in this module intentionally avoid database-specific details:
no SQL strings, table names, cursors, or SQLite connection objects should leak
through this boundary.
"""

from typing import Protocol

from src.core.context.models import AgentContextState
from src.core.finalization.models import CompletedTurn
from src.core.maintenance.models import MaintenanceJobSpec
from src.core.workspace.models import SessionContext


class ConversationHistoryStore(Protocol):
    """Append and read conversation history without exposing its storage format."""

    def append_turn(self, completed: CompletedTurn) -> list[str]:
        """Persist all messages produced by one completed Turn."""

    def load_turn(self, session: SessionContext, turn_index: int) -> tuple[list, list[str]]:
        """Load one committed Turn and return messages plus their durable IDs."""

    def rebuild_recent(self, session: SessionContext) -> int:
        """Rebuild compact recent context from durable history."""


class SessionStore(Protocol):
    """Read and update Session metadata and compact context."""

    def load_context(self, session: SessionContext) -> tuple[AgentContextState, int]:
        """Return compact context and latest committed turn index."""

    def save_fast_context(self, completed: CompletedTurn) -> None:
        """Update recent context and turn index during the minimal commit."""


class ExecutionStore(Protocol):
    """Persist Execution and Slice lifecycle transitions."""

    def finish_completed_turn(self, completed: CompletedTurn) -> None:
        """Mark the final Slice and Execution as completed when applicable."""


class MaintenanceQueue(Protocol):
    """Durably enqueue derived background work."""

    def enqueue(self, spec: MaintenanceJobSpec) -> str:
        """Enqueue one idempotent maintenance job."""


class StateUnitOfWork(Protocol):
    """One atomic state transaction for foreground business facts."""

    history: ConversationHistoryStore
    sessions: SessionStore
    executions: ExecutionStore
    maintenance: MaintenanceQueue

    def __enter__(self) -> "StateUnitOfWork": ...

    def __exit__(self, exc_type, exc, tb) -> bool | None: ...

    def commit(self) -> None:
        """Commit all changes made through this unit of work."""

    def rollback(self) -> None:
        """Abort all changes made through this unit of work."""


class StateUnitOfWorkFactory(Protocol):
    """Create Unit of Work instances for a concrete persistence backend."""

    def begin(self, store) -> StateUnitOfWork:
        """Start a new state transaction using the provided store adapter."""
