"""Compatibility facade for SQLite-backed local state stores."""

from uuid import UUID

from langchain_core.messages import SystemMessage

from src.config.settings import (
    MEMORY_BOOTSTRAP_LIMIT,
    MEMORY_MIN_IMPORTANCE,
    MEMORY_RETRIEVAL_LIMIT,
    POSTGRES_PROJECTION_ENABLED,
)
from src.core.context.models import AgentContextState
from src.core.adapters.sqlite.conversation_history import SQLiteConversationHistoryStore
from src.core.adapters.sqlite.memory_store import SQLiteMemoryRetrievalStore
from src.core.adapters.sqlite.memory_write_store import SQLiteMemoryWriteStore
from src.core.adapters.sqlite.session_store import SQLiteSessionStore
from src.core.adapters.sqlite.summary_store import SQLiteSummaryStore
from src.core.memory.extractor import MemoryCandidateExtractor
from src.core.memory.models import RetrievedMemory
from src.core.state.database import LocalStateDatabase
from src.core.workspace.models import SessionContext


MEMORY_MESSAGE_PREFIX = "Relevant long-term memory for this workspace:"


class LocalStateStore:
    """Authoritative local facade matching the Agent service persistence contract."""

    def __init__(
        self,
        database: LocalStateDatabase,
        *,
        retrieval_limit: int = MEMORY_RETRIEVAL_LIMIT,
        min_importance: int = MEMORY_MIN_IMPORTANCE,
        model_provider=None,
        projection_enabled: bool = POSTGRES_PROJECTION_ENABLED,
    ) -> None:
        self.database = database
        self.extractor = MemoryCandidateExtractor(model_provider)
        self.history = SQLiteConversationHistoryStore(database)
        self.sessions = SQLiteSessionStore(database)
        self.memory_retrieval = SQLiteMemoryRetrievalStore(
            database,
            retrieval_limit=retrieval_limit,
        )
        self.memory_writer = SQLiteMemoryWriteStore(
            database,
            extractor=self.extractor,
            min_importance=min_importance,
            projection_enabled=projection_enabled,
        )
        self.summaries = SQLiteSummaryStore(database)

    def initialize(self) -> None:
        self.database.initialize()

    def close(self) -> None:
        """Connections are short-lived, so the facade owns no open resource."""

    def load_session(self, session: SessionContext) -> tuple[AgentContextState, int]:
        return self.sessions.load_context(session)

    def save_session(self, session: SessionContext, state: AgentContextState, turn_index: int) -> None:
        with self.database.transaction() as conn:
            self.save_session_in_transaction(conn, session, state, turn_index)

    def archive_turn_messages(self, session: SessionContext, turn_index: int, messages: list) -> list[str]:
        with self.database.transaction() as conn:
            return self.append_messages_in_transaction(conn, session, turn_index, messages)

    def append_messages_in_transaction(
        self,
        conn,
        session: SessionContext,
        turn_index: int,
        messages: list,
        *,
        execution_id: str | None = None,
    ) -> list[str]:
        """Append messages using a caller-owned Unit of Work transaction."""
        return SQLiteConversationHistoryStore(
            self.database,
            transaction_conn=conn,
        ).append_messages(
            session,
            turn_index,
            messages,
            execution_id=execution_id,
        )

    def save_session_in_transaction(
        self,
        conn,
        session: SessionContext,
        state: AgentContextState,
        turn_index: int,
    ) -> None:
        """Update compact Session state using a caller-owned transaction."""
        SQLiteSessionStore(
            self.database,
            transaction_conn=conn,
        ).save_context(session, state, turn_index)

    def save_fast_session_in_transaction(
        self,
        conn,
        session: SessionContext,
        state: AgentContextState,
        turn_index: int,
    ) -> None:
        """Commit recent messages without overwriting a concurrent derived summary."""
        SQLiteSessionStore(
            self.database,
            transaction_conn=conn,
        ).save_fast_context_values(session, state, turn_index)

    def rebuild_recent_messages_from_archive(self, session: SessionContext) -> int:
        """Rebuild ``recent_messages`` from archived message history.

        Loads the most recent ``RECENT_MESSAGE_LIMIT`` messages from the
        ``messages`` table, deserialises their ``raw`` JSON via
        ``messages_from_dict``, and writes them back into the session row.
        ``context_tokens`` is reset to 0 so the next compression decision is
        based on fresh token estimates.

        Returns the number of recovered messages.
        """
        return self.history.rebuild_recent(session)

    def load_turn_messages(self, session: SessionContext, turn_index: int) -> tuple[list, list[str]]:
        """Load one committed Turn for a durable maintenance handler."""
        return self.history.load_turn(session, turn_index)

    def load_summary_source(
        self,
        session: SessionContext,
        target_turn: int,
    ) -> tuple[str, int, list[tuple[int, object]]]:
        """Load unsummarized committed messages up to a target Turn."""
        return self.summaries.load_summary_source(session, target_turn)

    def update_summary_cas(
        self,
        session: SessionContext,
        *,
        expected_summary_through_turn: int,
        summary_through_turn: int,
        summary: str,
    ) -> bool:
        """Write a derived summary only when no newer summary won the race."""
        return self.summaries.update_summary_cas(
            session,
            expected_summary_through_turn=expected_summary_through_turn,
            summary_through_turn=summary_through_turn,
            summary=summary,
        )

    def retrieve_relevant(self, workspace_id: UUID, query: str, limit: int | None = None) -> list[RetrievedMemory]:
        return self.memory_retrieval.retrieve_relevant(workspace_id, query, limit)

    def retrieve_bootstrap(self, workspace_id: UUID, limit: int = MEMORY_BOOTSTRAP_LIMIT) -> list[RetrievedMemory]:
        return self.memory_retrieval.retrieve_bootstrap(workspace_id, limit)

    def retrieve_for_turn(self, workspace_id: UUID, query: str, *, new_session: bool) -> list[RetrievedMemory]:
        return self.memory_retrieval.retrieve_for_turn(
            workspace_id,
            query,
            new_session=new_session,
        )

    def extract_and_save_memories(
        self,
        session: SessionContext,
        turn_index: int,
        messages: list,
        source_message_ids: list[str],
    ) -> list[str]:
        return self.memory_writer.extract_and_save(
            session,
            turn_index,
            messages,
            source_message_ids,
        )

    def build_memory_message(self, memories: list[RetrievedMemory]) -> SystemMessage | None:
        return self.memory_retrieval.build_memory_message(memories)

    def format_memories(self, memories: list[RetrievedMemory]) -> str:
        return self.memory_retrieval.format_memories(memories)
