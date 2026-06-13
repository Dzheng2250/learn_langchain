"""Ambient telemetry identity propagation."""

from contextvars import ContextVar, Token

from src.core.agent.models import AgentRunContext
from src.core.telemetry.models import TelemetryContext


_context: ContextVar[TelemetryContext] = ContextVar(
    "telemetry_context",
    default=TelemetryContext(),
)


def bind_context(
    *,
    workspace_id=None,
    session_id=None,
    turn_index: int | None = None,
    run_id: str = "",
) -> Token:
    """Bind identity inherited by telemetry emitted in the current context."""
    return _context.set(
        TelemetryContext(
            workspace_id=workspace_id,
            session_id=session_id,
            turn_index=turn_index,
            run_id=run_id,
        )
    )


def bind_run_context(run_context: AgentRunContext) -> Token:
    """Bind identity from the canonical Agent run context."""
    return bind_context(
        workspace_id=run_context.workspace.workspace_id,
        session_id=run_context.session.session_id,
        turn_index=run_context.turn_index,
        run_id=run_context.run_id,
    )


def current_context() -> TelemetryContext:
    """Return identity inherited by the current task or worker context."""
    return _context.get()


def reset_context(token: Token) -> None:
    """Restore the context that existed before :func:`bind_context`."""
    _context.reset(token)
