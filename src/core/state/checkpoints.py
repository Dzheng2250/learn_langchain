"""Lifecycle wrapper for LangGraph's durable SQLite checkpointer."""

from pathlib import Path

from src.config.paths import checkpoint_db


class CheckpointManager:
    """Own the SQLite saver context used by all Workspace graphs."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = ":memory:" if path == ":memory:" else Path(path or checkpoint_db()).expanduser().resolve()
        self._context = None
        self.saver = None

    def initialize(self):
        """Open and return the shared checkpointer with a clear dependency error."""
        if self.saver is not None:
            return self.saver
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as exc:
            raise RuntimeError(
                "Resumable execution requires 'langgraph-checkpoint-sqlite'. "
                "Install the project dependencies before starting Core."
            ) from exc
        if self.path != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._context = SqliteSaver.from_conn_string(str(self.path))
        self.saver = self._context.__enter__()
        self.saver.setup()
        return self.saver

    def delete_thread(self, thread_id: str) -> None:
        if self.saver is None:
            raise RuntimeError("Checkpoint manager must be initialized before deleting a thread.")
        self.saver.delete_thread(thread_id)

    def thread_exists(self, thread_id: str) -> bool:
        """Return whether LangGraph has at least one durable checkpoint for a thread."""
        if self.saver is None:
            raise RuntimeError("Checkpoint manager must be initialized before recovery reconciliation.")
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        return self.saver.get_tuple(config) is not None

    def close(self) -> None:
        if self._context is not None:
            self._context.__exit__(None, None, None)
        self._context = None
        self.saver = None
