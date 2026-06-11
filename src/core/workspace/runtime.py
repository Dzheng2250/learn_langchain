"""Thread-safe workspace runtime factory and cache."""

from dataclasses import dataclass
from threading import Lock
from uuid import UUID

from src.core.agent.graph import create_parent_graph
from src.core.tools.registry import WorkspaceToolset, create_workspace_toolset
from src.core.workspace.models import WorkspaceContext


@dataclass(frozen=True)
class WorkspaceRuntime:
    workspace: WorkspaceContext
    toolset: WorkspaceToolset
    graph: object


class WorkspaceRuntimeFactory:
    def create(self, workspace: WorkspaceContext) -> WorkspaceRuntime:
        toolset = create_workspace_toolset(workspace)
        return WorkspaceRuntime(workspace, toolset, create_parent_graph(toolset.parent_tools, toolset.skill_manifest))


class WorkspaceRuntimeRegistry:
    def __init__(self, factory: WorkspaceRuntimeFactory | None = None) -> None:
        self.factory = factory or WorkspaceRuntimeFactory()
        self._lock = Lock()
        self._runtimes: dict[UUID, WorkspaceRuntime] = {}
        self._creation_locks: dict[UUID, Lock] = {}

    def get(self, workspace: WorkspaceContext) -> WorkspaceRuntime:
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
