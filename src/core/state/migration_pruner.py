"""PostgreSQL source pruning for local-state migration."""

from collections.abc import Callable

from src.core.state.migration_models import LocalStateMigrationReport
from src.core.workspace.resolver import canonical_path_key


class LocalStateSourcePruner:
    """Delete PostgreSQL rows outside the retained migrated Session."""

    def __init__(self, connection_factory) -> None:
        self.connection_factory = connection_factory

    def prune(
        self,
        report: LocalStateMigrationReport,
        validate: Callable[[object, LocalStateMigrationReport], None] | None = None,
    ) -> None:
        """Prune source rows and validate retained counts in one transaction."""
        with self.connection_factory() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT s.workspace_id, s.session_id
                        FROM agent_sessions s
                        JOIN agent_workspaces w ON w.workspace_id = s.workspace_id
                        WHERE w.canonical_path = %s AND s.session_name = %s
                        """,
                        (canonical_path_key(report.workspace), report.session_name),
                    )
                    identity = cur.fetchone()
                    if not identity:
                        raise RuntimeError("Retained PostgreSQL Session disappeared before pruning.")
                    workspace_id, session_id = identity

                    # Remove source links from other Sessions first. Memories
                    # are retained only when at least one source is a message
                    # belonging to the selected default Session.
                    cur.execute(
                        """
                        DELETE FROM agent_memory_sources src
                        WHERE NOT EXISTS (
                            SELECT 1 FROM agent_messages msg
                            WHERE msg.workspace_id = src.workspace_id
                              AND msg.id = src.message_id
                              AND msg.session_id = %s
                        )
                        """,
                        (session_id,),
                    )
                    cur.execute(
                        """
                        DELETE FROM agent_memories m
                        WHERE m.workspace_id <> %s OR NOT EXISTS (
                            SELECT 1 FROM agent_memory_sources src
                            WHERE src.workspace_id = m.workspace_id
                              AND src.memory_id = m.id
                        )
                        """,
                        (workspace_id,),
                    )
                    # Session deletion cascades its messages and Session-bound
                    # events. Lifecycle events have no Session and are not part
                    # of the retained default conversation.
                    cur.execute(
                        "DELETE FROM agent_events WHERE session_id IS NULL OR session_id <> %s",
                        (session_id,),
                    )
                    cur.execute("DELETE FROM agent_sessions WHERE session_id <> %s", (session_id,))
                    cur.execute("DELETE FROM agent_workspaces WHERE workspace_id <> %s", (workspace_id,))
                    (validate or self.validate_source_prune)(cur, report)

    def validate_source_prune(self, cur, expected: LocalStateMigrationReport) -> None:
        """Abort source pruning unless retained PostgreSQL counts match SQLite."""
        for table, count in (
            ("agent_sessions", expected.sessions),
            ("agent_messages", expected.messages),
            ("agent_memories", expected.memories),
            ("agent_events", expected.events),
        ):
            cur.execute(f"SELECT count(*) FROM {table}")
            actual = int(cur.fetchone()[0])
            if actual != count:
                raise RuntimeError(
                    f"PostgreSQL prune validation failed for {table}: "
                    f"expected {count}, got {actual}."
                )
