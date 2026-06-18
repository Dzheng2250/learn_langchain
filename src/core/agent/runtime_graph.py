"""Runtime graph selection for foreground Agent turns."""

from langchain_core.messages import HumanMessage

from src.core.workspace.runtime import WorkspaceRuntimeRegistry


class RuntimeGraphResolver:
    """Resolve the concrete graph for a turn without exposing runtime internals."""

    def __init__(self, runtime_registry: WorkspaceRuntimeRegistry) -> None:
        self.runtime_registry = runtime_registry

    def graph_for_turn(self, workspace, *, goal_mode: bool):
        """Return the parent graph variant for a new turn."""
        runtime = self.runtime_registry.get(workspace)
        return runtime.goal_graph if goal_mode else runtime.graph

    def graph_for_resume(self, workspace, pending, *, instruction: str = ""):
        """Return the graph for a pending execution and inject resume instruction."""
        graph = self.graph_for_turn(workspace, goal_mode=pending.goal_mode)
        if instruction.strip():
            graph.update_state(
                {"configurable": {"thread_id": pending.checkpoint_thread_id}},
                {
                    "messages": [
                        HumanMessage(
                            content=(
                                "Additional resume instruction: "
                                f"{instruction.strip()}"
                            )
                        )
                    ]
                },
                as_node="agent",
            )
        return graph
