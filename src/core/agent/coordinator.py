"""Application-level preparation and finalization for one Agent Turn."""

from src.core.agent.models import RunLimits
from src.core.context.loader import ConversationContextLoader, PreparedTurn


class TurnCoordinator:
    """Coordinate context preparation and the minimal durable completion boundary."""

    def __init__(
        self,
        context_loader: ConversationContextLoader,
        turn_finalizer,
    ) -> None:
        self.context_loader = context_loader
        self.turn_finalizer = turn_finalizer

    def prepare(
        self,
        *,
        session,
        user_input: str,
        run_id: str,
        limits: RunLimits,
    ) -> PreparedTurn:
        """Load bounded context and workspace memory before graph execution."""
        return self.context_loader.prepare(
            session=session,
            user_input=user_input,
            run_id=run_id,
            limits=limits,
        )

    def finalize(self, **kwargs):
        """Commit minimal durable state and enqueue derived maintenance."""
        if self.turn_finalizer is None:
            raise RuntimeError("Turn finalization is not configured.")
        return self.turn_finalizer.finalize(**kwargs)
