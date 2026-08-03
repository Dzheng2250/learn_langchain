"""Core daemon process entry point."""

import argparse
import asyncio
import os
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import psycopg

from src.config.environment import load_user_environment

# Console-script entry points import this module directly. Load the user-level
# environment before importing settings-backed Core components.
load_user_environment()

from src.config.paths import env_file, local_state_db
from src.core.app import CoreApp
from src.core.config.models import CoreConfig
from src.core.database.connection import connection_info
from src.core.database.migration import WorkspaceMigration
from src.core.state import (
    ArtifactStore, LocalStateDatabase, LocalStateMigration, downgrade_local_schema,
    validate_local_schema_downgrade,
)
from src.core.state.locking import local_state_operation_lock
from src.config.settings import CORE_HOST, CORE_PORT
from src.ipc.auth import create_token, daemon_pid_is_running, read_token, token_path


def _backup_local_state(database: LocalStateDatabase, backup: Path) -> None:
    """Create and validate one complete SQLite backup, removing partial output."""
    descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with database.connect() as source, sqlite3.connect(backup, timeout=5) as target:
            backup_deadline = time.monotonic() + 30

            def check_backup_deadline(_status, _remaining, _total):
                if time.monotonic() >= backup_deadline:
                    raise TimeoutError("Local-state backup exceeded 30 seconds.")

            source.backup(
                target,
                pages=256,
                progress=check_backup_deadline,
                sleep=0.05,
            )
            check_deadline = time.monotonic() + 30
            target.set_progress_handler(
                lambda: 1 if time.monotonic() >= check_deadline else 0,
                1_000,
            )
            try:
                check = target.execute("PRAGMA quick_check").fetchone()
            finally:
                target.set_progress_handler(None, 0)
            if check is None or check[0] != "ok":
                raise RuntimeError(f"Local-state backup integrity check failed: {check}")
    except Exception:
        backup.unlink(missing_ok=True)
        raise


async def serve(host: str = CORE_HOST, port: int = CORE_PORT) -> None:
    """Compose and run one Core daemon until a shutdown RPC is received."""
    config = CoreConfig.load(host=host, port=port)
    token = (
        read_token(config.runtime_dir)
        if token_path(config.runtime_dir).exists()
        else create_token(config.runtime_dir)
    )
    with local_state_operation_lock(local_state_db()):
        app = CoreApp(config, token)
        await app.run()


def main(argv: list[str] | None = None) -> int:
    """Dispatch Core-only lifecycle, configuration, and migration commands."""
    parser = argparse.ArgumentParser(prog="learn-agent-core")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve_parser = subparsers.add_parser("serve", help="run the Core daemon")
    serve_parser.add_argument("--host", default=CORE_HOST)
    serve_parser.add_argument("--port", type=int, default=CORE_PORT)
    config_parser = subparsers.add_parser("init-user-config", help="copy secrets to user-level config")
    config_parser.add_argument("--from-env", required=True)
    config_parser.add_argument("--force", action="store_true")
    migration_parser = subparsers.add_parser("migrate-workspace", help="migrate a legacy database")
    migration_parser.add_argument("--workspace", required=True)
    migration_parser.add_argument("--keep-session", default="default")
    migration_parser.add_argument("--apply", action="store_true")
    local_parser = subparsers.add_parser(
        "migrate-local-state",
        help="import one PostgreSQL Session into authoritative local SQLite state",
    )
    local_parser.add_argument("--workspace", required=True)
    local_parser.add_argument("--keep-session", default="default")
    local_parser.add_argument("--apply", action="store_true")
    local_parser.add_argument(
        "--prune-source",
        action="store_true",
        help="after a validated import, delete PostgreSQL rows unrelated to the retained Session",
    )
    rollback_parser = subparsers.add_parser(
        "rollback-local-state",
        help="offline rollback of one explicitly supported local schema transition",
    )
    rollback_parser.add_argument("--from-version", type=int, required=True)
    rollback_parser.add_argument("--to-version", type=int, required=True)
    rollback_parser.add_argument("--apply", action="store_true")
    gc_parser = subparsers.add_parser("gc-artifacts", help="delete unreferenced local artifacts")
    args = parser.parse_args(argv)

    if args.command == "serve":
        asyncio.run(serve(args.host, args.port))
        return 0
    if args.command == "init-user-config":
        source = Path(args.from_env).expanduser().resolve(strict=True)
        target = env_file()
        if target.exists() and not args.force:
            raise RuntimeError(f"Refusing to overwrite existing user config: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        print(f"Initialized user config: {target}")
        return 0
    if args.command == "migrate-workspace":
        runtime = CoreConfig.load().runtime_dir
        if daemon_pid_is_running(runtime):
            raise RuntimeError("Core daemon is running. Stop the daemon before migration.")
        migration = WorkspaceMigration(lambda: psycopg.connect(connection_info()))
        report = (
            migration.apply(args.workspace, args.keep_session)
            if args.apply
            else migration.inspect(args.workspace, args.keep_session)
        )
        print(
            f"{'Applied' if report.applied else 'Dry-run'} migration for {report.workspace}: "
            f"sessions={report.sessions}, messages={report.messages}, "
            f"memories={report.memories}, events={report.events}"
        )
        if report.backup_path:
            print(f"Backup: {report.backup_path}")
        return 0
    if args.command == "migrate-local-state":
        if args.prune_source and not args.apply:
            raise RuntimeError("--prune-source requires --apply.")
        runtime = CoreConfig.load().runtime_dir
        if daemon_pid_is_running(runtime):
            raise RuntimeError("Core daemon is running. Stop the daemon before migration.")
        migration = LocalStateMigration(lambda: psycopg.connect(connection_info()))
        if args.apply:
            with local_state_operation_lock(local_state_db()):
                report = migration.apply(
                    args.workspace,
                    args.keep_session,
                    prune_source=args.prune_source,
                )
        else:
            report = migration.inspect(args.workspace, args.keep_session)
        print(
            f"{'Applied' if report.applied else 'Dry-run'} local-state migration for "
            f"{report.workspace}/{report.session_name}: sessions={report.sessions}, "
            f"messages={report.messages}, memories={report.memories}, events={report.events}"
        )
        print(
            "Would delete from PostgreSQL: "
            + ", ".join(f"{name}={count}" for name, count in report.deleted_counts.items())
        )
        print(f"Local state: {report.target_path}")
        if report.source_pruned:
            print("PostgreSQL source was pruned to the retained Session.")
        if report.backup_path:
            print(f"PostgreSQL backup: {report.backup_path}")
        return 0
    if args.command == "rollback-local-state":
        runtime = CoreConfig.load().runtime_dir
        if daemon_pid_is_running(runtime):
            raise RuntimeError("Core daemon is running. Stop the daemon before rollback.")
        database = LocalStateDatabase()
        with local_state_operation_lock(database.path):
            if not database.path.exists():
                raise FileNotFoundError(f"Local state database does not exist: {database.path}")
            with database.connect() as conn:
                current = int(conn.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM local_schema_migrations"
                ).fetchone()[0])
            if current != args.from_version:
                raise RuntimeError(
                    f"Expected local schema v{args.from_version}, found v{current}."
                )
            validate_local_schema_downgrade(
                from_version=args.from_version,
                to_version=args.to_version,
            )
            if not args.apply:
                print(
                    f"Dry-run local schema rollback: v{args.from_version} -> "
                    f"v{args.to_version}; database={database.path}"
                )
                return 0
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            suffix = uuid4().hex[:8]
            backup = database.path.with_name(
                database.path.name + f".v{current}-backup-{stamp}-{suffix}"
            )
            _backup_local_state(database, backup)
            with database.transaction() as conn:
                downgrade_local_schema(
                    conn, from_version=args.from_version, to_version=args.to_version
                )
            print(
                f"Rolled back local schema v{args.from_version} -> v{args.to_version}. "
                f"Backup: {backup}"
            )
            return 0
    if args.command == "gc-artifacts":
        runtime = CoreConfig.load().runtime_dir
        if daemon_pid_is_running(runtime):
            raise RuntimeError("Core daemon is running. Stop the daemon before artifact GC.")
        database = LocalStateDatabase()
        with local_state_operation_lock(database.path):
            database.initialize()
            deleted = ArtifactStore(database).collect_garbage()
        print(f"Deleted {deleted} unreferenced artifacts.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
