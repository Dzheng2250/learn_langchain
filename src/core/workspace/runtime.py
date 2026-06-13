"""Thread-safe workspace runtime factory and cache."""

from dataclasses import dataclass
from threading import Lock
from uuid import UUID

from src.core.agent.models import RunLimits
from src.core.agent.graph import create_parent_graph
from src.core.llm.provider import ModelProvider, OpenAICompatibleProvider
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
        model_provider: ModelProvider | None = None,
        run_limits: RunLimits | None = None,
    ) -> None:
        self.model_provider = model_provider or OpenAICompatibleProvider()
        self.run_limits = run_limits or RunLimits()

    def create(self, workspace: WorkspaceContext) -> WorkspaceRuntime:
        """Build Workspace-bound tools and compile the parent Agent graph."""
        # Every factory below receives the immutable workspace root. Tools and
        # graphs therefore cannot be rebound by mutating process-global state.
        toolset = create_workspace_toolset(
            workspace,
            self.model_provider,
            subagent_max_steps=self.run_limits.max_subagent_steps,
        )
        graph = create_parent_graph(
            toolset.parent_tools,
            toolset.skill_manifest,
            self.model_provider,
        )
        return WorkspaceRuntime(workspace, toolset, graph)


class WorkspaceRuntimeRegistry:
    """Thread-safe cache that prevents duplicate per-Workspace compilation."""

    def __init__(self, factory: WorkspaceRuntimeFactory | None = None) -> None:
        self.factory = factory or WorkspaceRuntimeFactory()
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
