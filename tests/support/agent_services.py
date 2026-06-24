"""Test-only assembly helpers for Agent application services."""

from src.config.settings import CORE_AGENT_WORKERS, MAX_AUTO_SLICES_PER_GRANT, MEMORY_ENABLED
from src.core.agent.async_runner import AgentAsyncTurnRunner
from src.core.agent.coordinator import TurnCoordinator
from src.core.agent.locking import SessionLockRegistry
from src.core.agent.loop import LoopConfig, TurnExecutionLoop
from src.core.agent.loop_errors import TurnLoopErrorHandler
from src.core.agent.loop_pause import TurnLoopPauseHandler
from src.core.agent.models import RunLimits
from src.core.agent.request_stream import AgentRequestStreamService
from src.core.agent.run_observer import TurnRunObserver
from src.core.agent.runtime_graph import RuntimeGraphResolver
from src.core.agent.service import AgentTurnService
from src.core.agent.service_lifecycle import AgentServiceLifecycle
from src.core.agent.slices import SliceExecutionService
from src.core.agent.sync_runner import AgentSyncTurnRunner
from src.core.agent.worker import TurnWorkerExecutor
from src.core.context.loader import ConversationContextLoader
from src.core.context.manager import AgentContextManager
from src.core.diagnostics import DiagnosticTurnService
from src.core.errors import ProviderErrorHandler
from src.core.errors.provider_failure import ProviderFailureService
from src.core.execution import ExecutionLifecycleService
from src.core.llm.provider import AnthropicProvider
from src.core.workspace.session_adapter import RepositoryAgentSessionStore
from tests.support.model_providers import UnusedModelProvider


def build_agent_turn_service(
    *,
    workspace_repository,
    runtime_registry,
    state_store_factory,
    session_context_store=None,
    memory_retrieval_store=None,
    model_configuration=None,
    execution_repository=None,
    turn_coordinator=None,
    turn_worker=None,
    sync_turn_runner=None,
    lock_registry=None,
    run_limits=None,
    turn_finalizer=None,
    checkpoint_manager=None,
    maintenance_repository=None,
    maintenance_scheduler=None,
    recovery_coordinator=None,
    memory_enabled=MEMORY_ENABLED,
    max_concurrent_turns=CORE_AGENT_WORKERS,
    max_auto_slices=MAX_AUTO_SLICES_PER_GRANT,
) -> AgentTurnService:
    """Assemble the full Agent graph for integration tests with selective fakes."""
    context_manager = AgentContextManager(UnusedModelProvider())
    model_configuration = model_configuration or AnthropicProvider()
    lock_registry = lock_registry or SessionLockRegistry()
    run_limits = run_limits or RunLimits()
    turn_worker = turn_worker or TurnWorkerExecutor(max_workers=max_concurrent_turns)
    runtime_graph_resolver = RuntimeGraphResolver(runtime_registry)
    execution_lifecycle = ExecutionLifecycleService(execution_repository)
    provider_failure_service = ProviderFailureService(
        execution_repository=execution_repository,
        maintenance_repository=maintenance_repository,
        maintenance_scheduler=maintenance_scheduler,
    )
    context_store = state_store_factory()
    session_context_store = session_context_store or getattr(
        context_store,
        "sessions",
        context_store,
    )
    memory_retrieval_store = memory_retrieval_store or getattr(
        context_store,
        "memory_retrieval",
        context_store,
    )
    context_loader = ConversationContextLoader(
        context_manager,
        session_store=session_context_store,
        memory_store=memory_retrieval_store,
        memory_enabled=memory_enabled,
    )
    provider_error_handler = ProviderErrorHandler()
    diagnostic_turn_service = DiagnosticTurnService(
        session_store=session_context_store,
        run_limits=run_limits,
    )
    slice_execution_service = SliceExecutionService(
        execution_store=execution_repository,
        provider_error_handler=provider_error_handler,
    )
    turn_coordinator = turn_coordinator or TurnCoordinator(
        context_loader,
        turn_finalizer,
    )
    turn_run_observer = TurnRunObserver()
    turn_execution_loop = TurnExecutionLoop(
        turn_coordinator=turn_coordinator,
        run_limits=run_limits,
        slice_execution_service=slice_execution_service,
        observer=turn_run_observer,
        error_handler=TurnLoopErrorHandler(
            execution_store=execution_repository,
            provider_failure_service=provider_failure_service,
            observer=turn_run_observer,
        ),
        pause_handler=TurnLoopPauseHandler(
            execution_store=execution_repository,
            observer=turn_run_observer,
        ),
        config=LoopConfig(max_auto_slices=max_auto_slices),
    )
    request_stream_service = AgentRequestStreamService(
        session_store=RepositoryAgentSessionStore(workspace_repository),
        lock_registry=lock_registry,
        model_configuration=model_configuration,
        diagnostic_turn_service=diagnostic_turn_service,
        execution_lifecycle=execution_lifecycle,
        runtime_graph_resolver=runtime_graph_resolver,
        turn_execution_loop=turn_execution_loop,
    )
    sync_turn_runner = sync_turn_runner or AgentSyncTurnRunner(request_stream_service)
    async_turn_runner = AgentAsyncTurnRunner(
        turn_worker=turn_worker,
        sync_runner=sync_turn_runner,
    )
    service_lifecycle = AgentServiceLifecycle(
        state_initializer=context_store,
        turn_worker=turn_worker,
        checkpoint_manager=checkpoint_manager,
        maintenance_scheduler=maintenance_scheduler,
        recovery_coordinator=recovery_coordinator,
    )
    return AgentTurnService(
        async_turn_runner=async_turn_runner,
        request_stream_service=request_stream_service,
        service_lifecycle=service_lifecycle,
    )
