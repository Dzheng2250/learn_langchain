"""Thread-safe workspace runtime factory and cache."""

from dataclasses import dataclass
from collections.abc import Callable
from threading import Lock
from uuid import UUID

from src.core.agent.models import RunLimits
from src.core.agent.graph import create_parent_graph
from src.core.llm.contracts import ModelProvider
from src.core.tasks.service import TaskPlanningService
from src.core.tools.registry import WorkspaceToolset, create_workspace_toolset
from src.core.workspace.models import WorkspaceContext


@dataclass(frozen=True)
class WorkspaceRuntime:
    """Cached tools and compiled graph permanently bound to one Workspace."""

    workspace: WorkspaceContext
    toolset: WorkspaceToolset
    graph: object


class WorkspaceRuntimeFactory:
    """Compose a complete immutable runtime for one Workspace."""

    def __init__(
        self,
        model_provider: ModelProvider,
        run_limits: RunLimits | None = None,
        checkpointer=None,
        checkpointer_provider: Callable[[], object] | None = None,
        task_service: TaskPlanningService | None = None,
        approval_repository=None,
        host_execution_enabled: bool = False,
        approval_mode_resolver=None,
        approval_strategy_registry=None,
        default_timeout_seconds: float = 60.0,
        network_policy: str = "deny",
        file_write_enabled: bool = True,
        command_write_enabled: bool = False,
        file_write_max_bytes: int = 1_048_576,
        file_operation_max_entries: int = 100,
        command_changeset_max_files: int = 100,
        command_changeset_max_bytes: int = 10_485_760,
        hook_runtime=None,
        resource_activity_recorder=None,
    ) -> None:
        self.model_provider = model_provider
        self.run_limits = run_limits or RunLimits()
        self.checkpointer = checkpointer
        self.checkpointer_provider = checkpointer_provider
        self.task_service = task_service
        self.approval_repository = approval_repository
        self.host_execution_enabled = host_execution_enabled
        self.approval_mode_resolver = approval_mode_resolver
        self.approval_strategy_registry = approval_strategy_registry
        self.default_timeout_seconds = default_timeout_seconds
        self.network_policy = network_policy
        self.file_write_enabled = file_write_enabled
        self.command_write_enabled = command_write_enabled
        self.file_write_max_bytes = file_write_max_bytes
        self.file_operation_max_entries = file_operation_max_entries
        self.command_changeset_max_files = command_changeset_max_files
        self.command_changeset_max_bytes = command_changeset_max_bytes
        self.hook_runtime = hook_runtime
        self.resource_activity_recorder = resource_activity_recorder

    def create(self, workspace: WorkspaceContext) -> WorkspaceRuntime:
        """Build Workspace-bound tools and compile the parent Agent graph."""
        # Every factory below receives the immutable workspace root. Tools and
        # graphs therefore cannot be rebound by mutating process-global state.
        hook_dispatcher = (
            self.hook_runtime.get(workspace.root)
            if self.hook_runtime is not None else None
        )
        toolset = create_workspace_toolset(
            workspace,
            self.model_provider,
            subagent_max_steps=self.run_limits.max_subagent_steps,
            task_service=self.task_service,
            approval_repository=self.approval_repository,
            host_execution_enabled=self.host_execution_enabled,
            approval_mode_resolver=self.approval_mode_resolver,
            approval_strategy_registry=self.approval_strategy_registry,
            default_timeout_seconds=self.default_timeout_seconds,
            network_policy=self.network_policy,
            file_write_enabled=self.file_write_enabled,
            command_write_enabled=self.command_write_enabled,
            file_write_max_bytes=self.file_write_max_bytes,
            file_operation_max_entries=self.file_operation_max_entries,
            command_changeset_max_files=self.command_changeset_max_files,
            command_changeset_max_bytes=self.command_changeset_max_bytes,
            hook_dispatcher=hook_dispatcher,
            resource_activity_recorder=self.resource_activity_recorder,
        )
        checkpointer = (
            self.checkpointer_provider()
            if self.checkpointer_provider is not None
            else self.checkpointer
        )
        graph = create_parent_graph(
            toolset.parent_tools,
            toolset.skill_manifest,
            self.model_provider,
            checkpointer=checkpointer,
            risk_by_name={spec.name: spec.risk for spec in toolset.registry.specs()},
            tool_pipeline=toolset.pipeline,
        )
        return WorkspaceRuntime(workspace, toolset, graph)


class WorkspaceRuntimeRegistry:
    """Thread-safe cache that prevents duplicate per-Workspace compilation."""

    def __init__(self, factory: WorkspaceRuntimeFactory) -> None:
        self.factory = factory
        self._lock = Lock()
        self._runtimes: dict[UUID, WorkspaceRuntime] = {}
        self._creation_locks: dict[UUID, Lock] = {}

    def get(self, workspace: WorkspaceContext) -> WorkspaceRuntime:
        """Return a cached runtime or create exactly one for the Workspace."""
        # Fast path reads the cache under the registry lock. A per-workspace
        # creation lock prevents duplicate graph compilation without blocking
        # unrelated workspaces.
        with self._lock:
            runtime = self._runtimes.get(workspace.workspace_id)
            creation_lock = self._creation_locks.setdefault(workspace.workspace_id, Lock())
        if runtime is not None:
            return runtime
        with creation_lock:
            with self._lock:
                runtime = self._runtimes.get(workspace.workspace_id)
            if runtime is None:
                runtime = self.factory.create(workspace)
                with self._lock:
                    self._runtimes[workspace.workspace_id] = runtime
                    self._creation_locks.pop(workspace.workspace_id, None)
            return runtime
