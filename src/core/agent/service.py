"""Worker-backed facade for foreground Agent turn execution."""

from collections.abc import Callable, Iterator
from threading import Lock, RLock
from uuid import UUID

from src.config.settings import (
    CORE_AGENT_WORKERS,
    MAX_AUTO_SLICES_PER_GRANT,
    MEMORY_ENABLED,
)
from src.core.agent.contracts import EventCallback, ExecutionControl
from src.core.agent.coordinator import TurnCoordinator
from src.core.agent.loop import TurnExecutionLoop
from src.core.agent.models import RunLimits
from src.core.agent.request_stream import AgentRequestStreamService
from src.core.agent.result import TurnResultBuilder
from src.core.agent.runtime_graph import RuntimeGraphResolver
from src.core.agent.slices import SliceExecutionService
from src.core.agent.worker import TurnWorkerExecutor
from src.core.state.contracts import StateStore
from src.core.context.loader import ConversationContextLoader
from src.core.context.manager import AgentContextManager
from src.core.diagnostics import DiagnosticTurnService
from src.core.execution import ExecutionLifecycleService
from src.core.errors import ProviderErrorHandler
from src.core.errors.provider_failure import ProviderFailureService
from src.core.llm.provider import ModelConfiguration, OpenAICompatibleProvider
from src.core.workspace.contracts import WorkspaceIdentityRepository
from src.core.workspace.runtime import WorkspaceRuntimeRegistry


class SessionLockRegistry:
    """Create and retain one reentrant consistency lock per Session UUID."""

    def __init__(self) -> None:
        self._guard = Lock()
        self._locks: dict[UUID, RLock] = {}

    def get(self, session_id: UUID) -> RLock:
        """Return the stable lock that serializes the given Session."""
        with self._guard:
            return self._locks.setdefault(session_id, RLock())


class AgentTurnService:
    """Run bounded synchronous Agent turns without blocking the Core event loop.

    A dedicated executor limits concurrent turns. Session UUID locks additionally
    serialize requests targeting the same Session while allowing different
    Sessions to run concurrently.
    """

    def __init__(
        self,
        *,
        workspace_repository: WorkspaceIdentityRepository,
        runtime_registry: WorkspaceRuntimeRegistry,
        state_store_factory: Callable[[], StateStore],
        context_manager: AgentContextManager | None = None,
        model_configuration: ModelConfiguration | None = None,
        memory_enabled: bool = MEMORY_ENABLED,
        lock_registry: SessionLockRegistry | None = None,
        run_limits: RunLimits | None = None,
        turn_worker: TurnWorkerExecutor | None = None,
        max_concurrent_turns: int = CORE_AGENT_WORKERS,
        execution_repository=None,
        checkpoint_manager=None,
        turn_finalizer=None,
        maintenance_repository=None,
        maintenance_scheduler=None,
        recovery_coordinator=None,
        turn_coordinator=None,
        context_loader: ConversationContextLoader | None = None,
        execution_lifecycle: ExecutionLifecycleService | None = None,
        provider_failure_service: ProviderFailureService | None = None,
        diagnostic_turn_service: DiagnosticTurnService | None = None,
        slice_execution_service: SliceExecutionService | None = None,
        turn_execution_loop: TurnExecutionLoop | None = None,
        runtime_graph_resolver: RuntimeGraphResolver | None = None,
        request_stream_service: AgentRequestStreamService | None = None,
        max_auto_slices: int = MAX_AUTO_SLICES_PER_GRANT,
        provider_error_handler: ProviderErrorHandler | None = None,
    ) -> None:
        self.workspace_repository = workspace_repository
        self.runtime_graph_resolver = runtime_graph_resolver or RuntimeGraphResolver(
            runtime_registry,
        )
        self.state_store_factory = state_store_factory
        self.context_manager = context_manager or AgentContextManager()
        self.model_configuration = model_configuration or OpenAICompatibleProvider()
        self.memory_enabled = memory_enabled
        self.lock_registry = lock_registry or SessionLockRegistry()
        self.run_limits = run_limits or RunLimits()
        self.turn_worker = turn_worker or TurnWorkerExecutor(
            max_workers=max_concurrent_turns,
        )
        self.execution_repository = execution_repository
        self.checkpoint_manager = checkpoint_manager
        self.turn_finalizer = turn_finalizer
        self.maintenance_repository = maintenance_repository
        self.maintenance_scheduler = maintenance_scheduler
        self.recovery_coordinator = recovery_coordinator
        self.execution_lifecycle = execution_lifecycle or ExecutionLifecycleService(
            self.execution_repository
        )
        self.provider_failure_service = provider_failure_service or ProviderFailureService(
            execution_repository=self.execution_repository,
            maintenance_repository=self.maintenance_repository,
            maintenance_scheduler=self.maintenance_scheduler,
        )
        self.context_loader = context_loader or ConversationContextLoader(
            self.context_manager,
            memory_enabled=self.memory_enabled,
        )
        self.max_auto_slices = max(1, int(max_auto_slices))
        self.provider_error_handler = provider_error_handler or ProviderErrorHandler()
        self.diagnostic_turn_service = diagnostic_turn_service or DiagnosticTurnService(
            state_store_factory=self.state_store_factory,
            run_limits=self.run_limits,
        )
        self.slice_execution_service = slice_execution_service or SliceExecutionService(
            execution_repository=self.execution_repository,
            provider_error_handler=self.provider_error_handler,
        )
        self.turn_coordinator = turn_coordinator or TurnCoordinator(
            self.context_loader,
            self.turn_finalizer,
        )
        self.turn_execution_loop = turn_execution_loop or TurnExecutionLoop(
            state_store_factory=self.state_store_factory,
            turn_coordinator=self.turn_coordinator,
            run_limits=self.run_limits,
            execution_repository=self.execution_repository,
            slice_execution_service=self.slice_execution_service,
            provider_failure_service=self.provider_failure_service,
            max_auto_slices=self.max_auto_slices,
        )
        self.request_stream_service = request_stream_service or AgentRequestStreamService(
            workspace_repository=self.workspace_repository,
            lock_registry=self.lock_registry,
            model_configuration=self.model_configuration,
            diagnostic_turn_service=self.diagnostic_turn_service,
            execution_lifecycle=self.execution_lifecycle,
            runtime_graph_resolver=self.runtime_graph_resolver,
            turn_execution_loop=self.turn_execution_loop,
            execution_repository=self.execution_repository,
        )

    def initialize(self) -> None:
        """Initialize durable schema dependencies before accepting requests."""
        store = self.state_store_factory()
        try:
            store.initialize()
            if self.checkpoint_manager is not None:
                self.checkpoint_manager.initialize()
            if self.recovery_coordinator is not None:
                self.recovery_coordinator.reconcile()
            if self.maintenance_scheduler is not None:
                self.maintenance_scheduler.start()
        finally:
            store.close()

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
        """Schedule one synchronous turn on the bounded Agent executor.

        ``on_event`` is invoked from the worker thread. RPC adapters must marshal
        socket writes back to their owning event loop.
        """
        return await self.turn_worker.run(
            self._run_turn_sync,
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
        return await self.turn_worker.run(
            self._run_resume_sync,
            workspace_root,
            session_name,
            instruction,
            on_event,
            run_id=run_id,
            control=control,
        )

    def _run_turn_sync(
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
        """Consume one synchronous event stream and aggregate its final result."""
        result = TurnResultBuilder(run_id=run_id, default_error="Agent turn failed.")
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
        return result.build()

    def _run_resume_sync(
        self,
        workspace_root: str,
        session_name: str,
        instruction: str = "",
        on_event: EventCallback | None = None,
        *,
        run_id: str | None = None,
        control: ExecutionControl | None = None,
    ) -> dict:
        """Consume one resumed execution stream and aggregate its final result."""
        result = TurnResultBuilder(run_id=run_id, default_error="Agent resume failed.")
        for item in self.request_stream_service.stream_resume(
            workspace_root,
            session_name,
            instruction=instruction,
            run_id=result.run_id,
            control=control,
        ):
            if on_event:
                on_event(item)
            result.observe(item)
        return result.build()

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
        """Compatibility wrapper for tests and internal synchronous callers."""
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
        """Compatibility wrapper for tests and internal synchronous callers."""
        yield from self.request_stream_service.stream_resume(
            workspace_root,
            session_name,
            run_id=run_id,
            instruction=instruction,
            control=control,
        )

    def close(self) -> None:
        """Stop foreground Turn workers, then stop durable maintenance safely."""
        self.turn_worker.close()
        maintenance_stopped = (
            self.maintenance_scheduler.close()
            if self.maintenance_scheduler is not None
            else True
        )
        if self.checkpoint_manager is not None and maintenance_stopped:
            self.checkpoint_manager.close()
