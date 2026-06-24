"""Worker-backed facade for foreground Agent turn execution."""

from collections.abc import Iterator

from src.core.agent.async_runner import AgentAsyncTurnRunner
from src.core.agent.contracts import EventCallback, ExecutionControl
from src.core.agent.request_stream import AgentRequestStreamService
from src.core.agent.service_lifecycle import AgentServiceLifecycle


class AgentTurnService:
    """Expose Agent turns while delegating execution, streaming, and lifecycle.

    All concrete dependencies are assembled by the process composition root.
    This facade does not create providers, stores, repositories, workers, or
    maintenance services.
    """

    def __init__(
        self,
        *,
        async_turn_runner: AgentAsyncTurnRunner,
        request_stream_service: AgentRequestStreamService,
        service_lifecycle: AgentServiceLifecycle,
    ) -> None:
        self.async_turn_runner = async_turn_runner
        self.request_stream_service = request_stream_service
        self.service_lifecycle = service_lifecycle

    def initialize(self) -> None:
        """Initialize durable schema dependencies before accepting requests."""
        self.service_lifecycle.initialize()

    async def run_turn(
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
        """Schedule one synchronous turn on the bounded Agent executor."""
        return await self.async_turn_runner.run_turn(
            workspace_root,
            session_name,
            user_input,
            on_event,
            run_id=run_id,
            control=control,
            goal_mode=goal_mode,
        )

    async def resume_execution(
        self,
        workspace_root: str,
        session_name: str,
        instruction: str = "",
        on_event: EventCallback | None = None,
        *,
        run_id: str | None = None,
        control: ExecutionControl | None = None,
    ) -> dict:
        """Schedule a recoverable execution resume on the bounded executor."""
        return await self.async_turn_runner.resume(
            workspace_root,
            session_name,
            instruction,
            on_event,
            run_id=run_id,
            control=control,
        )

    def stream_turn(
        self,
        workspace_root: str,
        session_name: str,
        user_input: str,
        *,
        run_id: str,
        control: ExecutionControl | None = None,
        goal_mode: bool = False,
    ) -> Iterator[dict]:
        """Compatibility wrapper for synchronous internal callers."""
        yield from self.request_stream_service.stream_turn(
            workspace_root,
            session_name,
            user_input,
            run_id=run_id,
            control=control,
            goal_mode=goal_mode,
        )

    def stream_resume(
        self,
        workspace_root: str,
        session_name: str,
        *,
        run_id: str,
        instruction: str = "",
        control: ExecutionControl | None = None,
    ) -> Iterator[dict]:
        """Compatibility wrapper for synchronous internal callers."""
        yield from self.request_stream_service.stream_resume(
            workspace_root,
            session_name,
            run_id=run_id,
            instruction=instruction,
            control=control,
        )

    def close(self) -> None:
        """Close resources owned by the injected service lifecycle."""
        self.service_lifecycle.close()
