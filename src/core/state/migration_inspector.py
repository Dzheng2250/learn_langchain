"""PostgreSQL inspection for local-state migration."""

from pathlib import Path

from src.core.state.migration_models import LocalStateMigrationReport
from src.core.workspace.resolver import canonical_path_key, canonicalize_workspace


class LocalStateMigrationInspector:
    """Read source PostgreSQL counts for the retained Workspace Session."""

    def __init__(self, connection_factory, target_path: Path) -> None:
        self.connection_factory = connection_factory
        self.target_path = target_path

    def inspect(self, workspace: str | Path, keep_session: str = "default") -> LocalStateMigrationReport:
        """Read retained source counts without creating or modifying local state."""
        root = canonicalize_workspace(workspace)
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT s.workspace_id, s.session_id
                    FROM agent_sessions s
                    JOIN agent_workspaces w ON w.workspace_id = s.workspace_id
                    WHERE w.canonical_path = %s AND s.session_name = %s
                    """,
                    (canonical_path_key(root), keep_session),
                )
                identity = cur.fetchone()
                if not identity:
                    raise RuntimeError(
                        f"No PostgreSQL Session named {keep_session!r} exists for Workspace {root}."
                    )
                _workspace_id, session_id = identity
                cur.execute("SELECT count(*) FROM agent_messages WHERE session_id = %s", (session_id,))
                messages = int(cur.fetchone()[0])
                cur.execute(
                    """
                    SELECT count(DISTINCT m.id)
                    FROM agent_memories m
                    JOIN agent_memory_sources src
                      ON src.workspace_id = m.workspace_id AND src.memory_id = m.id
                    JOIN agent_messages msg
                      ON msg.workspace_id = src.workspace_id AND msg.id = src.message_id
                    WHERE msg.session_id = %s
                    """,
                    (session_id,),
                )
                memories = int(cur.fetchone()[0])
                cur.execute("SELECT count(*) FROM agent_events WHERE session_id = %s", (session_id,))
                events = int(cur.fetchone()[0])
                source_counts = {}
                for name, table in (
                    ("sessions", "agent_sessions"),
                    ("messages", "agent_messages"),
                    ("memories", "agent_memories"),
                    ("events", "agent_events"),
                ):
                    cur.execute(f"SELECT count(*) FROM {table}")
                    source_counts[name] = int(cur.fetchone()[0])
        return LocalStateMigrationReport(
            root,
            keep_session,
            1,
            messages,
            memories,
            events,
            self.target_path,
            source_sessions=source_counts["sessions"],
            source_messages=source_counts["messages"],
            source_memories=source_counts["memories"],
            source_events=source_counts["events"],
        )
