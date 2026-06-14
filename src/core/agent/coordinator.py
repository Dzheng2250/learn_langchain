"""Application-level preparation and finalization for one Agent Turn."""

from dataclasses import dataclass

from src.core.agent.models import AgentRunContext, RunLimits


@dataclass(frozen=True)
class PreparedTurn:
    """Immutable inputs prepared before the LangGraph Slice loop starts."""

    state: object
    turn_index: int
    run_context: AgentRunContext
    input_messages: list


class TurnCoordinator:
    """Coordinate context preparation and the minimal durable completion boundary."""

    def __init__(self, context_manager, turn_finalizer, *, memory_enabled: bool = True) -> None:
        self.context_manager = context_manager
        self.turn_finalizer = turn_finalizer
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
        """Load bounded context and workspace memory before graph execution."""
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

    def finalize(self, **kwargs):
        """Commit minimal durable state and enqueue derived maintenance."""
        if self.turn_finalizer is None:
            raise RuntimeError("Turn finalization is not configured.")
        return self.turn_finalizer.finalize(**kwargs)
