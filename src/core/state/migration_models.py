"""Models used by PostgreSQL-to-local-state migration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LocalStateMigrationReport:
    """Counts and paths produced by a local-state migration inspection or apply."""

    workspace: Path
    session_name: str
    sessions: int
    messages: int
    memories: int
    events: int
    target_path: Path
    source_sessions: int = 0
    source_messages: int = 0
    source_memories: int = 0
    source_events: int = 0
    applied: bool = False
    source_pruned: bool = False
    backup_path: Path | None = None

    @property
    def deleted_counts(self) -> dict[str, int]:
        """Return the source rows removed when pruning to the retained Session."""
        return {
            "sessions": max(0, self.source_sessions - self.sessions),
            "messages": max(0, self.source_messages - self.messages),
            "memories": max(0, self.source_memories - self.memories),
            "events": max(0, self.source_events - self.events),
        }
