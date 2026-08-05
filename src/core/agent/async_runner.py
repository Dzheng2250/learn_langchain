"""Async facade that schedules synchronous Agent stream consumers."""

from src.core.agent.contracts import EventCallback, ExecutionControl
from src.core.agent.sync_runner import AgentSyncTurnRunner
from src.core.agent.worker import TurnWorkerExecutor


class AgentAsyncTurnRunner:
    """Run synchronous Agent turn consumers on the bounded worker executor."""

    def __init__(
        self,
        *,
        turn_worker: TurnWorkerExecutor,
        sync_runner: AgentSyncTurnRunner,
    ) -> None:
        self.turn_worker = turn_worker
        self.sync_runner = sync_runner

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
        """Schedule one foreground turn without blocking the event loop."""
        return await self.turn_worker.run(
            self.sync_runner.run_turn,
            workspace_root,
            session_name,
            user_input,
            on_event,
            run_id=run_id,
            control=control,
            goal_mode=goal_mode,
        )

    async def resume(
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
        """Schedule one recoverable execution resume."""
        return await self.turn_worker.run(
            self.sync_runner.resume,
            workspace_root,
            session_name,
            instruction,
            on_event,
            run_id=run_id,
            control=control,
            resume_value=resume_value,
            retry_conditions=retry_conditions,
        )
