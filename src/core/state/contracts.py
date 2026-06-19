"""Small typed persistence capabilities consumed by application services."""

from typing import Protocol
from uuid import UUID

from src.core.context.models import AgentContextState
from src.core.workspace.models import SessionContext


class StateStore(Protocol):
    """Only the state capabilities required by the foreground Turn path."""

    def initialize(self) -> None: ...

    def close(self) -> None: ...

    def load_session(self, session: SessionContext) -> tuple[AgentContextState, int]: ...

    def retrieve_for_turn(self, workspace_id: UUID, query: str, *, new_session: bool) -> list: ...

    def build_memory_message(self, memories: list): ...


class MaintenanceStateStore(Protocol):
    """Derived-state capabilities required only by background handlers."""

    def close(self) -> None: ...

    def load_summary_source(
        self,
        session: SessionContext,
        target_turn: int,
    ) -> tuple[str, int, list[tuple[int, object]]]: ...

    def update_summary_cas(
        self,
        session: SessionContext,
        *,
        expected_summary_through_turn: int,
        summary_through_turn: int,
        summary: str,
    ) -> bool: ...

    def load_turn_messages(
        self,
        session: SessionContext,
        turn_index: int,
    ) -> tuple[list, list[str]]: ...

    def extract_and_save_memories(
        self,
        session: SessionContext,
        turn_index: int,
        messages: list,
        source_message_ids: list[str],
    ) -> list[str]: ...


class CheckpointStore(Protocol):
    """LangGraph checkpoint lifecycle needed by recovery and cleanup."""

    def initialize(self): ...

    def delete_thread(self, thread_id: str) -> None: ...

    def thread_exists(self, thread_id: str) -> bool: ...

    def close(self) -> None: ...
