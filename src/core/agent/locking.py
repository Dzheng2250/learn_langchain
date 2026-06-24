"""Session-level concurrency guards for foreground Agent turns."""

from threading import Lock, RLock
from uuid import UUID


class SessionLockRegistry:
    """Create and retain one reentrant consistency lock per Session UUID."""

    def __init__(self) -> None:
        self._guard = Lock()
        self._locks: dict[UUID, RLock] = {}

    def get(self, session_id: UUID) -> RLock:
        """Return the stable lock that serializes the given Session."""
        with self._guard:
            return self._locks.setdefault(session_id, RLock())
