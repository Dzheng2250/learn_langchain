"""Dependency-injector container for Core service composition."""

from dependency_injector import containers, providers

from src.config.maintenance import MaintenanceSettings
from src.config.settings import (
    CORE_AGENT_WORKERS,
    MAX_AUTO_SLICES_PER_GRANT,
    MEMORY_ENABLED,
)
from src.core.adapters.sqlite import SQLiteStateUnitOfWorkFactory
from src.core.agent.coordinator import TurnCoordinator
from src.core.agent.loop import TurnExecutionLoop
from src.core.agent.models import RunLimits
from src.core.agent.request_stream import AgentRequestStreamService
from src.core.agent.runtime_graph import RuntimeGraphResolver
from src.core.agent.service import AgentTurnService, SessionLockRegistry
from src.core.agent.slices import SliceExecutionService
from src.core.agent.worker import TurnWorkerExecutor
from src.core.bus.router import RpcRouter
from src.core.config.models import CoreConfig
from src.core.container_factories import (
    create_default_event_bus,
    create_optional_postgres_pool,
    create_socket_transport,
    create_transport,
    maintenance_handlers,
)
from src.core.context.loader import ConversationContextLoader
from src.core.context.manager import AgentContextManager
from src.core.diagnostics import DiagnosticTurnService
from src.core.errors import ProviderErrorHandler
from src.core.errors.provider_failure import ProviderFailureService
from src.core.execution import ExecutionLifecycleService
from src.core.finalization import CompletedTurnCommitter, TurnFinalizer
from src.core.handlers import AgentHandlers, CoreHandlers
from src.core.llm.provider import OpenAICompatibleProvider
from src.core.maintenance import (
    ExecutionRecoveryCoordinator,
    MaintenanceRepository,
    MaintenanceScheduler,
)
from src.core.maintenance.handlers import (
    CheckpointCleanupHandler,
    ContextSummaryHandler,
    MemoryExtractionHandler,
)
from src.core.session import SessionLifecycleService
from src.core.state import (
    CheckpointManager,
    ExecutionRepository,
    LocalStateDatabase,
    LocalStateStore,
    LocalWorkspaceRepository,
)
from src.core.tasks import TaskPlanningService, TaskRepository
from src.core.workspace.runtime import WorkspaceRuntimeFactory, WorkspaceRuntimeRegistry
from src.core.tracing import TracingModelProvider


class CoreContainer(containers.DeclarativeContainer):
    """Dependency graph for one Core daemon process.

    The container is deliberately kept at the composition boundary. Business
    services still receive explicit constructor dependencies and do not import
    this module.
    """

    config = providers.Dependency(instance_of=CoreConfig)
    auth_token = providers.Dependency(instance_of=str)
    shutdown_event = providers.Dependency()
    trace_recorder = providers.Object(None)
    transport_factory = providers.Object(create_socket_transport)

    state_database = providers.Singleton(LocalStateDatabase)
    checkpoint_manager = providers.Singleton(CheckpointManager)
    postgres_pool = providers.Singleton(create_optional_postgres_pool)
    event_bus = providers.Singleton(
        create_default_event_bus,
        pool=postgres_pool,
        trace_recorder=trace_recorder,
    )

    base_model_provider = providers.Singleton(OpenAICompatibleProvider)
    model_provider = providers.Singleton(
        TracingModelProvider,
        inner=base_model_provider,
    )
    run_limits = providers.Factory(RunLimits)
    memory_enabled = providers.Object(MEMORY_ENABLED)
    maintenance_settings = providers.Singleton(MaintenanceSettings.load)
    provider_error_handler = providers.Factory(ProviderErrorHandler)
    session_lock_registry = providers.Singleton(SessionLockRegistry)
    turn_worker = providers.Factory(
        TurnWorkerExecutor,
        max_workers=CORE_AGENT_WORKERS,
    )

    workspace_repository = providers.Singleton(
        LocalWorkspaceRepository,
        database=state_database,
    )
    execution_repository = providers.Singleton(
        ExecutionRepository,
        database=state_database,
    )
    execution_lifecycle_service = providers.Factory(
        ExecutionLifecycleService,
        execution_repository=execution_repository,
    )
    maintenance_repository = providers.Singleton(
        MaintenanceRepository,
        database=state_database,
        settings=maintenance_settings,
    )
    task_repository = providers.Singleton(
        TaskRepository,
        database=state_database,
    )
    task_service = providers.Factory(
        TaskPlanningService,
        repository=task_repository,
    )
    context_manager = providers.Factory(
        AgentContextManager,
        model_provider=model_provider,
    )
    context_loader = providers.Factory(
        ConversationContextLoader,
        context_manager=context_manager,
        memory_enabled=memory_enabled,
    )
    state_store_factory = providers.Factory(
        LocalStateStore,
        database=state_database,
        model_provider=model_provider,
    )

    context_summary_handler = providers.Factory(
        ContextSummaryHandler,
        workspace_repository=workspace_repository,
        store_factory=state_store_factory.provider,
        context_manager=context_manager,
    )
    memory_extraction_handler = providers.Factory(
        MemoryExtractionHandler,
        workspace_repository=workspace_repository,
        store_factory=state_store_factory.provider,
    )
    checkpoint_cleanup_handler = providers.Factory(
        CheckpointCleanupHandler,
        checkpoint_manager=checkpoint_manager,
        execution_repository=execution_repository,
    )
    maintenance_handlers = providers.Callable(
        maintenance_handlers,
        context_summary_handler=context_summary_handler,
        memory_extraction_handler=memory_extraction_handler,
        checkpoint_cleanup_handler=checkpoint_cleanup_handler,
    )
    maintenance_scheduler = providers.Singleton(
        MaintenanceScheduler,
        repository=maintenance_repository,
        handlers=maintenance_handlers,
        settings=maintenance_settings,
    )
    provider_failure_service = providers.Factory(
        ProviderFailureService,
        execution_repository=execution_repository,
        maintenance_repository=maintenance_repository,
        maintenance_scheduler=maintenance_scheduler,
    )
    diagnostic_turn_service = providers.Factory(
        DiagnosticTurnService,
        state_store_factory=state_store_factory.provider,
        run_limits=run_limits,
    )
    slice_execution_service = providers.Factory(
        SliceExecutionService,
        execution_repository=execution_repository,
        provider_error_handler=provider_error_handler,
    )
    unit_of_work_factory = providers.Factory(
        SQLiteStateUnitOfWorkFactory,
        database=state_database,
        execution_repository=execution_repository,
        maintenance_repository=maintenance_repository,
    )
    completed_turn_committer = providers.Factory(
        CompletedTurnCommitter,
        unit_of_work_factory=unit_of_work_factory,
    )
    turn_finalizer = providers.Factory(
        TurnFinalizer,
        context_manager=context_manager,
        committer=completed_turn_committer,
        maintenance_scheduler=maintenance_scheduler,
    )
    turn_coordinator = providers.Factory(
        TurnCoordinator,
        context_loader=context_loader,
        turn_finalizer=turn_finalizer,
    )
    turn_execution_loop = providers.Singleton(
        TurnExecutionLoop,
        state_store_factory=state_store_factory.provider,
        turn_coordinator=turn_coordinator,
        run_limits=run_limits,
        execution_repository=execution_repository,
        slice_execution_service=slice_execution_service,
        provider_failure_service=provider_failure_service,
        max_auto_slices=MAX_AUTO_SLICES_PER_GRANT,
    )
    recovery_coordinator = providers.Factory(
        ExecutionRecoveryCoordinator,
        execution_repository=execution_repository,
        checkpoint_manager=checkpoint_manager,
        maintenance_repository=maintenance_repository,
    )

    workspace_runtime_factory = providers.Factory(
        WorkspaceRuntimeFactory,
        model_provider=model_provider,
        run_limits=run_limits,
        checkpointer_provider=checkpoint_manager.provided.initialize,
        task_service=task_service,
    )
    runtime_registry = providers.Singleton(
        WorkspaceRuntimeRegistry,
        factory=workspace_runtime_factory,
    )
    runtime_graph_resolver = providers.Factory(
        RuntimeGraphResolver,
        runtime_registry=runtime_registry,
    )
    request_stream_service = providers.Factory(
        AgentRequestStreamService,
        workspace_repository=workspace_repository,
        lock_registry=session_lock_registry,
        model_configuration=model_provider,
        diagnostic_turn_service=diagnostic_turn_service,
        execution_lifecycle=execution_lifecycle_service,
        runtime_graph_resolver=runtime_graph_resolver,
        turn_execution_loop=turn_execution_loop,
        execution_repository=execution_repository,
    )
    agent_service = providers.Factory(
        AgentTurnService,
        workspace_repository=workspace_repository,
        runtime_registry=runtime_registry,
        state_store_factory=state_store_factory.provider,
        context_manager=context_manager,
        context_loader=context_loader,
        model_configuration=model_provider,
        lock_registry=session_lock_registry,
        turn_worker=turn_worker,
        run_limits=run_limits,
        turn_coordinator=turn_coordinator,
        execution_repository=execution_repository,
        checkpoint_manager=checkpoint_manager,
        turn_finalizer=turn_finalizer,
        execution_lifecycle=execution_lifecycle_service,
        provider_failure_service=provider_failure_service,
        diagnostic_turn_service=diagnostic_turn_service,
        slice_execution_service=slice_execution_service,
        turn_execution_loop=turn_execution_loop,
        runtime_graph_resolver=runtime_graph_resolver,
        request_stream_service=request_stream_service,
        maintenance_repository=maintenance_repository,
        maintenance_scheduler=maintenance_scheduler,
        recovery_coordinator=recovery_coordinator,
        provider_error_handler=provider_error_handler,
    )
    session_lifecycle_service = providers.Factory(
        SessionLifecycleService,
        workspace_repository=workspace_repository,
        state_store_factory=state_store_factory.provider,
        lock_registry=session_lock_registry,
        execution_repository=execution_repository,
        checkpoint_manager=checkpoint_manager,
        maintenance_repository=maintenance_repository,
        maintenance_scheduler=maintenance_scheduler,
    )

    router = providers.Singleton(
        RpcRouter,
        auth_token=auth_token,
    )
    core_handlers = providers.Factory(
        CoreHandlers,
        shutdown_event=shutdown_event,
    )
    agent_handlers = providers.Factory(
        AgentHandlers,
        agent_service=agent_service,
        session_service=session_lifecycle_service,
    )
    transport = providers.Factory(
        create_transport,
        config=config,
        router=router,
        transport_factory=transport_factory,
    )
