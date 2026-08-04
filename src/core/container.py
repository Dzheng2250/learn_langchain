"""Dependency-injector container for Core service composition."""

from dependency_injector import containers, providers

from src.config.maintenance import MaintenanceSettings
from src.config.hooks import HookSettings
from src.config.llm import LlmRetrySettings
from src.config.settings import (
    CORE_AGENT_WORKERS,
    MAX_AUTO_SLICES_PER_GRANT,
    MEMORY_ENABLED,
    MEMORY_MIN_IMPORTANCE,
    POSTGRES_PROJECTION_ENABLED,
    HOST_EXECUTION_ENABLED,
    TOOL_APPROVAL_MODE,
    TOOL_DEFAULT_TIMEOUT_SECONDS,
    TOOL_NETWORK_POLICY,
    FILE_WRITE_ENABLED,
    COMMAND_WRITE_ENABLED,
    FILE_WRITE_MAX_BYTES,
    FILE_OPERATION_MAX_ENTRIES,
    COMMAND_CHANGESET_MAX_FILES,
    COMMAND_CHANGESET_MAX_BYTES,
    RESOURCE_ACTIVITY_ENABLED,
    RESOURCE_ACTIVITY_HASH_ENABLED,
    RESOURCE_ACTIVITY_MAX_ITEMS_PER_EXECUTION,
)
from src.core.adapters.sqlite import (
    SQLiteConversationHistoryStore,
    SQLiteMemoryRetrievalStore,
    SQLiteMemoryWriteStore,
    SQLiteSessionStore,
    SQLiteSummaryStore,
    SQLiteToolApprovalRepository,
    SQLiteStateUnitOfWorkFactory,
)
from src.core.adapters.sqlite.session_lifecycle import SQLiteSessionLifecycleStore
from src.core.agent.async_runner import AgentAsyncTurnRunner
from src.core.agent.coordinator import TurnCoordinator
from src.core.agent.loop import LoopConfig, TurnExecutionLoop
from src.core.agent.loop_errors import TurnLoopErrorHandler
from src.core.agent.loop_pause import TurnLoopPauseHandler
from src.core.agent.models import RunLimits
from src.core.agent.request_stream import AgentRequestStreamService
from src.core.agent.run_observer import TurnRunObserver
from src.core.agent.runtime_graph import RuntimeGraphResolver
from src.core.agent.locking import SessionLockRegistry
from src.core.agent.service import AgentTurnService
from src.core.agent.service_lifecycle import AgentServiceLifecycle
from src.core.agent.slices import SliceExecutionService
from src.core.agent.sync_runner import AgentSyncTurnRunner
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
from src.core.context.compaction import ContextCompactionService
from src.core.diagnostics import DiagnosticTurnService
from src.core.errors import ProviderErrorHandler
from src.core.errors.provider_failure import ProviderFailureService
from src.core.execution import ExecutionLifecycleService
from src.core.finalization import CompletedTurnCommitter, TurnFinalizer
from src.core.handlers import AgentHandlers, CoreHandlers
from src.core.hooks import HookRuntimeRegistry
from src.core.llm.provider import AnthropicProvider
from src.core.llm.resilience import ResilientModelProvider
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
from src.core.memory.extractor import MemoryCandidateExtractor
from src.core.resource_activity import ResourceActivityQueryService
from src.core.adapters.sqlite.resource_activity import SQLiteResourceActivityRepository
from src.core.session import SessionHistoryQueryService, SessionLifecycleService
from src.core.session.checkpoint_cleanup import SessionCheckpointCleanupQueue
from src.core.session.status import SessionStatusReader
from src.core.state import (
    CheckpointManager,
    ExecutionRepository,
    LocalStateDatabase,
    LocalWorkspaceRepository,
)
from src.core.tasks import TaskPlanningService, TaskRepository
from src.core.tools.approval_service import ToolApprovalService
from src.core.tools.security import ApprovalModeResolver, ApprovalStrategyRegistry
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
    resource_activity_repository = providers.Singleton(
        SQLiteResourceActivityRepository, database=state_database,
        enabled=providers.Object(RESOURCE_ACTIVITY_ENABLED),
        hash_enabled=providers.Object(RESOURCE_ACTIVITY_HASH_ENABLED),
        max_items=providers.Object(RESOURCE_ACTIVITY_MAX_ITEMS_PER_EXECUTION),
    )
    checkpoint_manager = providers.Singleton(CheckpointManager)
    postgres_pool = providers.Singleton(create_optional_postgres_pool)
    event_bus = providers.Singleton(
        create_default_event_bus,
        pool=postgres_pool,
        trace_recorder=trace_recorder,
        config=config,
    )

    provider_error_handler = providers.Singleton(ProviderErrorHandler)
    llm_retry_settings = providers.Singleton(LlmRetrySettings.load)
    base_model_provider = providers.Singleton(AnthropicProvider)
    traced_model_provider = providers.Singleton(
        TracingModelProvider,
        inner=base_model_provider,
    )
    model_provider = providers.Singleton(
        ResilientModelProvider,
        inner=traced_model_provider,
        settings=llm_retry_settings,
        error_handler=provider_error_handler,
    )
    run_limits = providers.Factory(RunLimits)
    memory_enabled = providers.Object(MEMORY_ENABLED)
    maintenance_settings = providers.Singleton(MaintenanceSettings.load)
    session_lock_registry = providers.Singleton(SessionLockRegistry)
    hook_settings = providers.Singleton(HookSettings.load)
    hook_runtime = providers.Singleton(HookRuntimeRegistry, settings=hook_settings)
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
        execution_store=execution_repository,
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
    tool_approval_repository = providers.Singleton(
        SQLiteToolApprovalRepository,
        database=state_database,
    )
    approval_strategy_registry = providers.Singleton(ApprovalStrategyRegistry)
    approval_mode_resolver = providers.Singleton(
        ApprovalModeResolver,
        store=tool_approval_repository,
        registry=approval_strategy_registry,
        default_mode=providers.Object(TOOL_APPROVAL_MODE),
    )
    context_manager = providers.Factory(
        AgentContextManager,
        model_provider=model_provider,
    )

    conversation_history_store = providers.Factory(
        SQLiteConversationHistoryStore,
        database=state_database,
    )
    session_context_store = providers.Factory(
        SQLiteSessionStore,
        database=state_database,
    )
    memory_retrieval_store = providers.Factory(
        SQLiteMemoryRetrievalStore,
        database=state_database,
    )
    memory_candidate_extractor = providers.Factory(
        MemoryCandidateExtractor,
        model_provider=model_provider,
    )
    memory_write_store = providers.Factory(
        SQLiteMemoryWriteStore,
        database=state_database,
        extractor=memory_candidate_extractor,
        min_importance=MEMORY_MIN_IMPORTANCE,
        projection_enabled=POSTGRES_PROJECTION_ENABLED,
    )
    summary_store = providers.Factory(
        SQLiteSummaryStore,
        database=state_database,
    )
    context_compaction_service = providers.Factory(
        ContextCompactionService,
        context_manager=context_manager,
        session_store=session_context_store,
        summary_store=summary_store,
    )
    context_loader = providers.Factory(
        ConversationContextLoader,
        context_manager=context_manager,
        session_store=session_context_store,
        memory_store=memory_retrieval_store,
        memory_enabled=memory_enabled,
        compaction_service=context_compaction_service,
    )
    session_lifecycle_store = providers.Factory(
        SQLiteSessionLifecycleStore,
        workspace_repository=workspace_repository,
        history_store=conversation_history_store,
    )
    tool_approval_service = providers.Factory(
        ToolApprovalService,
        repository=tool_approval_repository,
        session_store=session_lifecycle_store,
        strategy_registry=approval_strategy_registry,
        default_mode=providers.Object(TOOL_APPROVAL_MODE),
    )

    context_summary_handler = providers.Factory(
        ContextSummaryHandler,
        workspace_repository=workspace_repository,
        summary_store=summary_store,
        context_manager=context_manager,
        hook_runtime=hook_runtime,
        compaction_service=context_compaction_service,
    )
    memory_extraction_handler = providers.Factory(
        MemoryExtractionHandler,
        workspace_repository=workspace_repository,
        history_store=conversation_history_store,
        memory_store=memory_write_store,
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
        session_store=session_context_store,
        run_limits=run_limits,
    )
    slice_execution_service = providers.Factory(
        SliceExecutionService,
        execution_store=execution_repository,
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
    turn_run_observer = providers.Singleton(TurnRunObserver)
    turn_loop_error_handler = providers.Factory(
        TurnLoopErrorHandler,
        execution_store=execution_repository,
        provider_failure_service=provider_failure_service,
        observer=turn_run_observer,
    )
    turn_loop_pause_handler = providers.Factory(
        TurnLoopPauseHandler,
        execution_store=execution_repository,
        observer=turn_run_observer,
    )
    loop_config = providers.Factory(
        LoopConfig,
        max_auto_slices=MAX_AUTO_SLICES_PER_GRANT,
    )
    turn_execution_loop = providers.Singleton(
        TurnExecutionLoop,
        turn_coordinator=turn_coordinator,
        run_limits=run_limits,
        slice_execution_service=slice_execution_service,
        observer=turn_run_observer,
        error_handler=turn_loop_error_handler,
        pause_handler=turn_loop_pause_handler,
        config=loop_config,
        hook_runtime=hook_runtime,
        task_service=task_service,
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
        approval_repository=tool_approval_repository,
        approval_mode_resolver=approval_mode_resolver,
        approval_strategy_registry=approval_strategy_registry,
        host_execution_enabled=providers.Object(HOST_EXECUTION_ENABLED),
        default_timeout_seconds=providers.Object(TOOL_DEFAULT_TIMEOUT_SECONDS),
        network_policy=providers.Object(TOOL_NETWORK_POLICY),
        file_write_enabled=providers.Object(FILE_WRITE_ENABLED),
        command_write_enabled=providers.Object(COMMAND_WRITE_ENABLED),
        file_write_max_bytes=providers.Object(FILE_WRITE_MAX_BYTES),
        file_operation_max_entries=providers.Object(FILE_OPERATION_MAX_ENTRIES),
        command_changeset_max_files=providers.Object(COMMAND_CHANGESET_MAX_FILES),
        command_changeset_max_bytes=providers.Object(COMMAND_CHANGESET_MAX_BYTES),
        hook_runtime=hook_runtime,
        resource_activity_recorder=resource_activity_repository,
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
        session_store=session_lifecycle_store,
        lock_registry=session_lock_registry,
        model_configuration=model_provider,
        diagnostic_turn_service=diagnostic_turn_service,
        execution_lifecycle=execution_lifecycle_service,
        runtime_graph_resolver=runtime_graph_resolver,
        turn_execution_loop=turn_execution_loop,
        hook_runtime=hook_runtime,
    )
    sync_turn_runner = providers.Factory(
        AgentSyncTurnRunner,
        request_stream_service=request_stream_service,
    )
    async_turn_runner = providers.Factory(
        AgentAsyncTurnRunner,
        turn_worker=turn_worker,
        sync_runner=sync_turn_runner,
    )
    agent_service_lifecycle = providers.Factory(
        AgentServiceLifecycle,
        state_initializer=state_database,
        turn_worker=turn_worker,
        checkpoint_manager=checkpoint_manager,
        maintenance_scheduler=maintenance_scheduler,
        recovery_coordinator=recovery_coordinator,
    )
    agent_service = providers.Factory(
        AgentTurnService,
        async_turn_runner=async_turn_runner,
        request_stream_service=request_stream_service,
        service_lifecycle=agent_service_lifecycle,
    )
    session_status_reader = providers.Factory(
        SessionStatusReader,
        lifecycle_store=session_lifecycle_store,
        session_store=session_context_store,
        execution_repository=execution_repository,
        maintenance_repository=maintenance_repository,
        approval_mode_service=tool_approval_service,
    )
    session_checkpoint_cleanup = providers.Factory(
        SessionCheckpointCleanupQueue,
        maintenance_repository=maintenance_repository,
        maintenance_scheduler=maintenance_scheduler,
    )
    session_lifecycle_service = providers.Factory(
        SessionLifecycleService,
        lifecycle_store=session_lifecycle_store,
        lock_registry=session_lock_registry,
        execution_repository=execution_repository,
        checkpoint_manager=checkpoint_manager,
        checkpoint_cleanup=session_checkpoint_cleanup,
        status_reader=session_status_reader,
    )
    session_history_service = providers.Factory(
        SessionHistoryQueryService,
        lifecycle_store=session_lifecycle_store,
        history_reader=conversation_history_store,
    )

    router = providers.Singleton(
        RpcRouter,
        auth_token=auth_token,
    )
    core_handlers = providers.Factory(
        CoreHandlers,
        shutdown_event=shutdown_event,
    )
    resource_activity_service = providers.Factory(
        ResourceActivityQueryService, repository=resource_activity_repository,
        workspace_repository=workspace_repository,
    )
    agent_handlers = providers.Factory(
        AgentHandlers,
        agent_service=agent_service,
        session_service=session_lifecycle_service,
        approval_service=tool_approval_service,
        resource_activity_service=resource_activity_service,
        session_history_service=session_history_service,
    )
    transport = providers.Factory(
        create_transport,
        config=config,
        router=router,
        transport_factory=transport_factory,
    )
