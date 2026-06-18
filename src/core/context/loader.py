"""Conversation context loading for one foreground Agent turn."""

from dataclasses import dataclass

from src.core.agent.models import AgentRunContext, RunLimits


@dataclass(frozen=True)
class PreparedTurn:
    """Immutable inputs prepared before the LangGraph Slice loop starts."""

    state: object
    turn_index: int
    run_context: AgentRunContext
    input_messages: list


class ConversationContextLoader:
    """Load persisted context, retrieve memory, and build graph input messages.

    This service isolates the read-side context policy from Agent execution.
    `AgentTurnService` should only ask for a prepared turn; it should not know
    whether memory came from SQLite, a file backend, or a future vector store.
    """

    def __init__(self, context_manager, *, memory_enabled: bool = True) -> None:
        self.context_manager = context_manager
        self.memory_enabled = memory_enabled

    def prepare(
        self,
        *,
        store,
        session,
        user_input: str,
        run_id: str,
        limits: RunLimits,
    ) -> PreparedTurn:
        """Load bounded conversation context and workspace memory."""
        state, completed_turn_index = store.load_session(session)
        current_turn = completed_turn_index + 1
        run_context = AgentRunContext(
            run_id=run_id,
            session=session,
            turn_index=current_turn,
            limits=limits,
        )
        memories = (
            store.retrieve_for_turn(
                session.workspace.workspace_id,
                user_input,
                new_session=completed_turn_index == 0,
            )
            if self.memory_enabled
            else []
        )
        memory_message = store.build_memory_message(memories)
        input_messages = self.context_manager.build_input_messages(
            state,
            user_input,
            extra_system_messages=[memory_message] if memory_message else [],
        )
        return PreparedTurn(state, current_turn, run_context, input_messages)
