"""Diagnostic turn service for validating Core without LLM configuration."""

from collections.abc import Iterator

from src.core.agent.models import AgentRunContext, RunLimits, StopReason
from src.core.ports import SessionStore
from src.core.telemetry import (
    bind_context,
    bind_run_context,
    emit_event,
    record_error,
    reset_context,
)
from src.core.workspace.models import SessionContext


class DiagnosticTurnService:
    """Stream the no-LLM diagnostic turn outside the main Agent service.

    The diagnostic turn proves that workspace/session resolution and local state
    access work even when model credentials are missing. It intentionally avoids
    mutating conversation history or incrementing turn_index.
    """

    def __init__(
        self,
        *,
        session_store: SessionStore,
        run_limits: RunLimits,
    ) -> None:
        self.session_store = session_store
        self.run_limits = run_limits

    def stream_unconfigured_turn(
        self,
        session: SessionContext,
        run_id: str,
        missing: tuple[str, ...],
    ) -> Iterator[dict]:
        """Validate infrastructure without mutating conversation state."""
        context_token = bind_context(
            workspace_id=session.workspace.workspace_id,
            session_id=session.session_id,
            run_id=run_id,
        )
        run_context_token = None
        try:
            _state, turn_index = self.session_store.load_context(session)
            run_context = AgentRunContext(
                run_id=run_id,
                session=session,
                turn_index=turn_index,
                limits=self.run_limits,
            )
            run_context_token = bind_run_context(run_context)
            emit_event(
                "diagnostic_started",
                "agent_service",
                "Started infrastructure diagnostic without LLM configuration.",
                {"session_name": session.session_name, "mode": "diagnostic"},
            )
            emit_event(
                "llm_configuration_missing",
                "agent_service",
                "LLM configuration is missing; returning a diagnostic response.",
                {"missing": list(missing)},
                level="warning",
            )
            response = (
                "Core infrastructure is running normally: CLI and daemon can communicate, "
                "the workspace and session resolve, and local state can be read.\n\n"
                "The model API key is not configured, so this diagnostic request did not "
                "write conversation history, increment turn_index, or call the LLM/tools. "
                "Set `LEARN_AGENT_LLM_API_KEY`; if the provider requires a custom endpoint, "
                "also set `LEARN_AGENT_LLM_BASE_URL`, then reinitialize user config and restart Core."
            )
            yield {"event": "token", "data": {"content": response}}
            emit_event(
                "diagnostic_finished",
                "agent_service",
                "Finished infrastructure diagnostic without LLM configuration.",
                {
                    "stop_reason": StopReason.LLM_NOT_CONFIGURED.value,
                    "tool_call_count": 0,
                    "mode": "diagnostic",
                },
            )
            yield {
                "event": "done",
                "data": {
                    "run_id": run_id,
                    "status": "ok",
                    "workspace_id": str(session.workspace.workspace_id),
                    "session_id": str(session.session_id),
                    "session_name": session.session_name,
                    "stop_reason": StopReason.LLM_NOT_CONFIGURED.value,
                    "tool_call_count": 0,
                },
            }
        except Exception as exc:
            record_error(
                "agent_service",
                "diagnostic_turn",
                exc,
                "Diagnostic turn failed.",
                event_type="turn_failed",
            )
            yield {
                "event": "error",
                "data": {
                    "type": "diagnostic_turn_failed",
                    "stop_reason": StopReason.TURN_ERROR.value,
                    "message": str(exc),
                    "run_id": run_id,
                },
            }
        finally:
            if run_context_token is not None:
                reset_context(run_context_token)
            reset_context(context_token)
