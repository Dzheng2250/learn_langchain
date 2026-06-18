"""SQLite Unit of Work adapter for foreground state commits."""

from src.core.finalization.models import CompletedTurn
from src.core.maintenance.repository import MaintenanceRepository
from src.core.state.database import LocalStateDatabase
from src.core.state.executions import ExecutionRepository


class SQLiteConversationHistoryStore:
    """SQLite-backed conversation history scoped to one open transaction."""

    def __init__(self, conn, store) -> None:
        self._conn = conn
        self._store = store

    def append_turn(self, completed: CompletedTurn) -> list[str]:
        return self._store.append_messages_in_transaction(
            self._conn,
            completed.session,
            completed.turn_index,
            completed.messages,
            execution_id=completed.execution_id,
        )

    def load_turn(self, session, turn_index: int) -> tuple[list, list[str]]:
        return self._store.load_turn_messages(session, turn_index)

    def rebuild_recent(self, session) -> int:
        return self._store.rebuild_recent_messages_from_archive(session)


class SQLiteSessionStore:
    """SQLite-backed Session metadata scoped to one open transaction."""

    def __init__(self, conn, store) -> None:
        self._conn = conn
        self._store = store

    def load_context(self, session):
        return self._store.load_session(session)

    def save_fast_context(self, completed: CompletedTurn) -> None:
        self._store.save_fast_session_in_transaction(
            self._conn,
            completed.session,
            completed.state,
            completed.turn_index,
        )


class SQLiteExecutionStore:
    """SQLite-backed Execution lifecycle scoped to one open transaction."""

    def __init__(self, conn, repository: ExecutionRepository) -> None:
        self._conn = conn
        self._repository = repository

    def finish_completed_turn(self, completed: CompletedTurn) -> None:
        if not completed.execution_id:
            return
        if completed.slice_id:
            self._repository.finish_slice_in_transaction(
                self._conn,
                completed.slice_id,
                completed.execution_id,
                graph_steps_used=completed.graph_steps_used,
                usage=completed.usage,
            )
        self._repository.complete_in_transaction(
            self._conn,
            completed.session,
            completed.execution_id,
        )


class SQLiteMaintenanceQueue:
    """SQLite-backed transactional outbox scoped to one open transaction."""

    def __init__(self, conn, repository: MaintenanceRepository) -> None:
        self._conn = conn
        self._repository = repository

    def enqueue(self, spec) -> str:
        return self._repository.enqueue_in_transaction(self._conn, spec)


class SQLiteStateUnitOfWork:
    """Own one SQLite transaction while exposing domain-level ports."""

    def __init__(
        self,
        database: LocalStateDatabase,
        store,
        execution_repository: ExecutionRepository,
        maintenance_repository: MaintenanceRepository,
    ) -> None:
        self._database = database
        self._store = store
        self._execution_repository = execution_repository
        self._maintenance_repository = maintenance_repository
        self._connect_context = None
        self._conn = None
        self._committed = False

    def __enter__(self):
        self._connect_context = self._database.connect()
        self._conn = self._connect_context.__enter__()
        self._conn.execute("BEGIN IMMEDIATE")
        self.history = SQLiteConversationHistoryStore(self._conn, self._store)
        self.sessions = SQLiteSessionStore(self._conn, self._store)
        self.executions = SQLiteExecutionStore(self._conn, self._execution_repository)
        self.maintenance = SQLiteMaintenanceQueue(self._conn, self._maintenance_repository)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None or not self._committed:
                self.rollback()
        finally:
            self._connect_context.__exit__(exc_type, exc, tb)
        return None

    def commit(self) -> None:
        self._conn.commit()
        self._committed = True

    def rollback(self) -> None:
        if self._conn is not None and not self._committed:
            self._conn.rollback()


class SQLiteStateUnitOfWorkFactory:
    """Create SQLite units of work for state.db-backed Core services."""

    def __init__(
        self,
        database: LocalStateDatabase,
        execution_repository: ExecutionRepository,
        maintenance_repository: MaintenanceRepository,
    ) -> None:
        self.database = database
        self.execution_repository = execution_repository
        self.maintenance_repository = maintenance_repository

    def begin(self, store) -> SQLiteStateUnitOfWork:
        return SQLiteStateUnitOfWork(
            self.database,
            store,
            self.execution_repository,
            self.maintenance_repository,
        )
