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
    goal_toolset: WorkspaceToolset
    goal_graph: object


class WorkspaceRuntimeFactory:
    """Compose a complete immutable runtime for one Workspace."""

    def __init__(
        self,
        model_provider: ModelProvider,
        run_limits: RunLimits | None = None,
        checkpointer=None,
        checkpointer_provider: Callable[[], object] | None = None,
        task_service: TaskPlanningService | None = None,
    ) -> None:
        self.model_provider = model_provider
        self.run_limits = run_limits or RunLimits()
        self.checkpointer = checkpointer
        self.checkpointer_provider = checkpointer_provider
        self.task_service = task_service

    def create(self, workspace: WorkspaceContext) -> WorkspaceRuntime:
        """Build Workspace-bound tools and compile the parent Agent graph."""
        # Every factory below receives the immutable workspace root. Tools and
        # graphs therefore cannot be rebound by mutating process-global state.
        toolset = create_workspace_toolset(
            workspace,
            self.model_provider,
            subagent_max_steps=self.run_limits.max_subagent_steps,
        )
        goal_toolset = create_workspace_toolset(
            workspace,
            self.model_provider,
            subagent_max_steps=self.run_limits.max_subagent_steps,
            task_service=self.task_service,
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
            task_planning_enabled=False,
        )
        goal_graph = create_parent_graph(
            goal_toolset.parent_tools,
            goal_toolset.skill_manifest,
            self.model_provider,
            checkpointer=checkpointer,
            risk_by_name={spec.name: spec.risk for spec in goal_toolset.registry.specs()},
            task_planning_enabled=self.task_service is not None,
        )
        return WorkspaceRuntime(workspace, toolset, graph, goal_toolset, goal_graph)


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
