"""Explicit PostgreSQL-to-local-state migration for one retained Session."""

import os
import shutil
from pathlib import Path
from uuid import uuid4

from src.config.paths import local_state_db
from src.core.database.backup import create_database_backup
from src.core.state.database import LocalStateDatabase
from src.core.state.migration_copier import LocalStateMigrationCopier
from src.core.state.migration_inspector import LocalStateMigrationInspector
from src.core.state.migration_models import LocalStateMigrationReport
from src.core.state.migration_pruner import LocalStateSourcePruner


class LocalStateMigration:
    """Import one Workspace-local PostgreSQL Session into a new SQLite authority."""

    def __init__(self, connection_factory, target_path: str | Path | None = None) -> None:
        self.connection_factory = connection_factory
        self.target_path = Path(target_path or local_state_db()).expanduser().resolve()
        self.inspector = LocalStateMigrationInspector(
            connection_factory,
            self.target_path,
        )
        self.copier = LocalStateMigrationCopier(connection_factory)
        self.pruner = LocalStateSourcePruner(connection_factory)

    def inspect(self, workspace: str | Path, keep_session: str = "default") -> LocalStateMigrationReport:
        """Read retained source counts without creating or modifying local state."""
        return self.inspector.inspect(workspace, keep_session)

    def apply(
        self,
        workspace: str | Path,
        keep_session: str = "default",
        *,
        prune_source: bool = False,
    ) -> LocalStateMigrationReport:
        """Back up PostgreSQL, build validated SQLite state, and optionally prune source."""
        report = self.inspect(workspace, keep_session)
        backup = create_database_backup()
        self.target_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.target_path.with_name(
            f".{self.target_path.name}.{uuid4().hex}.migration.tmp"
        )
        database = LocalStateDatabase(temporary)
        try:
            database.initialize()
            self._copy(report, database)
            self._validate(report, database)
            if self.target_path.exists():
                existing_backup = self.target_path.with_suffix(".pre_migration.bak")
                shutil.copy2(self.target_path, existing_backup)
            os.replace(temporary, self.target_path)
            if prune_source:
                self._prune_source(report)
        finally:
            database.close()
            for path in (
                temporary,
                Path(f"{temporary}-wal"),
                Path(f"{temporary}-shm"),
            ):
                path.unlink(missing_ok=True)
        return LocalStateMigrationReport(
            report.workspace,
            report.session_name,
            report.sessions,
            report.messages,
            report.memories,
            report.events,
            report.target_path,
            source_sessions=report.source_sessions,
            source_messages=report.source_messages,
            source_memories=report.source_memories,
            source_events=report.source_events,
            applied=True,
            source_pruned=prune_source,
            backup_path=backup,
        )

    def _prune_source(self, report: LocalStateMigrationReport) -> None:
        """Delete every PostgreSQL business row unrelated to the retained Session.

        This operation is intentionally separate from the SQLite copy. It runs
        only after the local authority has passed validation and uses one
        PostgreSQL transaction so a failed count check rolls back all deletes.
        """
        self.pruner.prune(report, validate=self._validate_source_prune)

    def _validate_source_prune(self, cur, expected: LocalStateMigrationReport) -> None:
        """Abort source pruning unless retained PostgreSQL counts match SQLite."""
        self.pruner.validate_source_prune(cur, expected)

    def _copy(self, report: LocalStateMigrationReport, database: LocalStateDatabase) -> None:
        self.copier.copy(report, database)

    def _validate(self, expected: LocalStateMigrationReport, database: LocalStateDatabase) -> None:
        with database.connect() as conn:
            actual = {
                "sessions": conn.execute("SELECT count(*) FROM sessions").fetchone()[0],
                "messages": conn.execute("SELECT count(*) FROM messages").fetchone()[0],
                "memories": conn.execute("SELECT count(*) FROM memories").fetchone()[0],
                "events": conn.execute("SELECT count(*) FROM imported_events").fetchone()[0],
            }
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Local-state migration produced foreign-key violations: {violations[:3]}")
        for name in actual:
            wanted = getattr(expected, name)
            if actual[name] != wanted:
                raise RuntimeError(
                    f"Local-state migration validation failed for {name}: expected {wanted}, got {actual[name]}."
                )
