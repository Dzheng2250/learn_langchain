"""Runtime graph selection for foreground Agent turns."""

from langchain_core.messages import HumanMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from src.core.workspace.contracts import WorkspaceRuntimeProvider


class RuntimeGraphResolver:
    """Resolve the concrete graph for a turn without exposing runtime internals."""

    def __init__(self, runtime_registry: WorkspaceRuntimeProvider) -> None:
        self.runtime_registry = runtime_registry

    def graph_for_turn(self, workspace, *, goal_mode: bool):
        """Return the stable parent graph; goal policy is turn-local input."""
        runtime = self.runtime_registry.get(workspace)
        return runtime.graph

    def graph_for_resume(self, workspace, pending, *, instruction: str = ""):
        """Return the graph for a pending execution and inject resume instruction."""
        graph = self.graph_for_turn(workspace, goal_mode=pending.goal_mode)
        config = {"configurable": {"thread_id": pending.checkpoint_thread_id}}
        self._repair_nested_messages(graph, config)
        if instruction.strip():
            # This is the deliberate LangGraph adapter boundary: application
            # text becomes a framework message only when graph state is updated.
            resume_message = HumanMessage(
                content=(
                    "Additional resume instruction: "
                    f"{instruction.strip()}"
                )
            )
            graph.update_state(
                config,
                {
                    "messages": [resume_message],
                    "turn_journal": [resume_message],
                },
                as_node="agent",
            )
        return graph

    @staticmethod
    def _repair_nested_messages(graph, config) -> None:
        """Flatten checkpoints written by the broken Goal continuation payload."""
        get_state = getattr(graph, "get_state", None)
        if not callable(get_state):
            return
        try:
            snapshot = get_state(config)
        except ValueError:
            if RuntimeGraphResolver._rollback_nested_pending_write(graph, config):
                return
            raise
        messages = list(getattr(snapshot, "values", {}).get("messages", []))
        flattened, changed = _flatten_nested_messages(messages)
        if not changed:
            return
        graph.update_state(
            config,
            {
                "messages": [
                    RemoveMessage(id=REMOVE_ALL_MESSAGES),
                    *flattened,
                ]
            },
            as_node="agent",
        )

    @staticmethod
    def _rollback_nested_pending_write(graph, config) -> bool:
        """Fork the last valid checkpoint when its pending write is malformed."""
        checkpointer = getattr(graph, "checkpointer", None)
        list_checkpoints = getattr(checkpointer, "list", None)
        if not callable(list_checkpoints):
            return False
        latest = next(iter(list_checkpoints(config)), None)
        if latest is None or not _contains_nested_message_write(latest.pending_writes):
            return False
        parent_config = getattr(latest, "parent_config", None)
        if not parent_config:
            return False
        graph.update_state(parent_config, {}, as_node="agent")
        return True


def _flatten_nested_messages(messages: list) -> tuple[list, bool]:
    """Flatten only malformed message wrappers; preserve valid message dicts."""
    flattened = []
    changed = False
    for message in messages:
        if (
            isinstance(message, dict)
            and "messages" in message
            and "role" not in message
            and "content" not in message
            and isinstance(message["messages"], list)
        ):
            nested, _ = _flatten_nested_messages(message["messages"])
            flattened.extend(nested)
            changed = True
            continue
        flattened.append(message)
    return flattened, changed


def _contains_nested_message_write(pending_writes) -> bool:
    """Detect only the malformed wrapper produced by the Goal continuation bug."""
    for write in pending_writes or ():
        if len(write) < 3 or write[1] != "messages":
            continue
        value = write[2]
        if (
            isinstance(value, dict)
            and "messages" in value
            and "role" not in value
            and "content" not in value
            and isinstance(value["messages"], list)
        ):
            return True
    return False
