"""SQLite connection and schema lifecycle for authoritative local state."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from src.config.paths import local_state_db


SCHEMA_VERSION = 1


class LocalStateDatabase:
    """Open short-lived WAL connections and initialize the local schema."""

    def __init__(self, path: str | Path | None = None, *, busy_timeout_ms: int = 5000) -> None:
        self.path = ":memory:" if path == ":memory:" else Path(path or local_state_db()).expanduser().resolve()
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))
        self._memory_lock = RLock()
        self._memory_conn = (
            sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
            if self.path == ":memory:"
            else None
        )
        if self._memory_conn is not None:
            self._memory_conn.row_factory = sqlite3.Row

    def initialize(self) -> None:
        """Create the authoritative local schema in one transaction."""
        if self.path != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        schema_path = Path(__file__).with_name("schema.sql")
        with self.connect() as conn:
            conn.executescript(schema_path.read_text(encoding="utf-8"))
            conn.execute(
                """
                INSERT INTO local_schema_migrations(version, name)
                VALUES (?, ?)
                ON CONFLICT(version) DO NOTHING
                """,
                (SCHEMA_VERSION, "local_first_state"),
            )

    def close(self) -> None:
        """Close the persistent in-memory test connection, if one is used."""
        if self._memory_conn is not None:
            self._memory_conn.close()
            self._memory_conn = None

    @contextmanager
    def connect(self):
        """Yield one thread-local connection configured for short transactions."""
        if self._memory_conn is not None:
            with self._memory_lock:
                yield self._memory_conn
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            # Some restricted/network filesystems reject WAL sidecar files.
            # DELETE mode preserves correctness for tests and degraded setups;
            # normal user-level local storage uses WAL.
            conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        """Yield one immediate write transaction and roll back on failure."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()
