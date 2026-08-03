"""Cross-process exclusion for operations that mutate authoritative local state."""

from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout


@contextmanager
def local_state_operation_lock(database_path: str | Path, *, timeout_seconds: float = 0):
    """Fail fast when another daemon or offline command owns local state."""
    database = Path(database_path).expanduser().resolve()
    lock_path = database.with_name(database.name + ".operation.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(lock_path))
    try:
        lock.acquire(timeout=max(0.0, float(timeout_seconds)))
    except Timeout as exc:
        raise RuntimeError(
            f"Local state is busy; another daemon or maintenance command holds {lock_path}."
        ) from exc
    try:
        yield
    finally:
        lock.release()
