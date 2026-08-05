"""Synchronous Agent stream consumers used inside bounded worker threads."""

from src.core.agent.contracts import EventCallback, ExecutionControl
from src.core.agent.request_stream import AgentRequestStreamService
from src.core.agent.result import TurnResultBuilder
from src.core.llm.retry_context import (
    bind_retry_event_callback,
    reset_retry_event_callback,
)


class AgentSyncTurnRunner:
    """Consume request event streams and aggregate final RPC result payloads."""

    def __init__(self, request_stream_service: AgentRequestStreamService) -> None:
        self.request_stream_service = request_stream_service

    def run_turn(
        self,
        workspace_root: str,
        session_name: str,
        user_input: str,
        on_event: EventCallback | None = None,
        *,
        run_id: str | None = None,
        control: ExecutionControl | None = None,
        goal_mode: bool = False,
    ) -> dict:
        """Consume one foreground turn stream."""
        result = TurnResultBuilder(run_id=run_id, default_error="Agent turn failed.")
        token = bind_retry_event_callback(on_event)
        try:
            for item in self.request_stream_service.stream_turn(
                workspace_root,
                session_name,
                user_input,
                run_id=result.run_id,
                control=control,
                goal_mode=goal_mode,
            ):
                if on_event:
                    on_event(item)
                result.observe(item)
        finally:
            reset_retry_event_callback(token)
        return result.build()

    def resume(
        self,
        workspace_root: str,
        session_name: str,
        instruction: str = "",
        on_event: EventCallback | None = None,
        *,
        run_id: str | None = None,
        control: ExecutionControl | None = None,
        resume_value: dict | None = None,
        retry_conditions: bool = False,
    ) -> dict:
        """Consume one resumed execution stream."""
        result = TurnResultBuilder(run_id=run_id, default_error="Agent resume failed.")
        token = bind_retry_event_callback(on_event)
        try:
            for item in self.request_stream_service.stream_resume(
                workspace_root,
                session_name,
                instruction=instruction,
                run_id=result.run_id,
                control=control,
                resume_value=resume_value,
                retry_conditions=retry_conditions,
            ):
                if on_event:
                    on_event(item)
                result.observe(item)
        finally:
            reset_retry_event_callback(token)
        return result.build()
