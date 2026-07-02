"""SQLite connection and schema lifecycle for authoritative local state."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, RLock

from src.config.paths import local_state_db
from src.core.state.migrations import LATEST_SCHEMA_VERSION, apply_local_migrations


SCHEMA_VERSION = LATEST_SCHEMA_VERSION


class LocalStateDatabase:
    """Open short-lived WAL connections and initialize the local schema."""

    def __init__(self, path: str | Path | None = None, *, busy_timeout_ms: int = 5000) -> None:
        self.path = ":memory:" if path == ":memory:" else Path(path or local_state_db()).expanduser().resolve()
        self.busy_timeout_ms = max(1, int(busy_timeout_ms))
        self._memory_lock = RLock()
        self._journal_lock = Lock()
        self._journal_configured = False
        self._memory_conn = (
            sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
            if self.path == ":memory:"
            else None
        )
        if self._memory_conn is not None:
            self._memory_conn.row_factory = sqlite3.Row
            self._memory_conn.execute("PRAGMA foreign_keys=ON")
            self._memory_conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            self._journal_configured = True

    def initialize(self) -> None:
        """Create the authoritative local schema in one transaction."""
        if self.path != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        schema_path = Path(__file__).with_name("schema.sql")
        with self.connect() as conn:
            try:
                # sqlite3.executescript() commits an existing transaction
                # before running. Starting the transaction inside the script
                # keeps fresh-schema creation and transactional upgrades atomic.
                conn.executescript(
                    "BEGIN IMMEDIATE;\n" + schema_path.read_text(encoding="utf-8")
                )
                conn.execute(
                    """
                    INSERT INTO local_schema_migrations(version, name)
                    VALUES (?, ?)
                    ON CONFLICT(version) DO NOTHING
                    """,
                    (1, "local_first_state"),
                )
                apply_local_migrations(conn)
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()

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
        self._configure_journal_mode(conn)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def read_transaction(self):
        """Yield one consistent read snapshot without reserving the writer lock."""
        with self.connect() as conn:
            conn.execute("BEGIN")
            try:
                yield conn
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()
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

    def _configure_journal_mode(self, conn) -> None:
        """Configure the database-wide journal once per process instance."""
        if self._journal_configured:
            return
        with self._journal_lock:
            if self._journal_configured:
                return
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                # Some restricted/network filesystems reject WAL sidecar
                # files. DELETE mode preserves correctness in degraded setups.
                conn.execute("PRAGMA journal_mode=DELETE")
            self._journal_configured = True
