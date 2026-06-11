"""Core daemon process entry point."""

import argparse
import asyncio
import os
import shutil
from pathlib import Path

import psycopg

from src.config.paths import env_file
from src.core.config.environment import load_core_environment
from src.core.app import CoreApp
from src.core.config.models import CoreConfig
from src.core.database.connection import connection_kwargs
from src.core.database.migration import WorkspaceMigration
from src.config.settings import CORE_HOST, CORE_PORT
from src.ipc.auth import create_token, daemon_pid_is_running, read_token, token_path


async def serve(host: str = CORE_HOST, port: int = CORE_PORT) -> None:
    config = CoreConfig.load(host=host, port=port)
    token = (
        read_token(config.runtime_dir)
        if token_path(config.runtime_dir).exists()
        else create_token(config.runtime_dir)
    )
    app = CoreApp(config, token)
    await app.run()


def main(argv: list[str] | None = None) -> int:
    load_core_environment()
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
        migration = WorkspaceMigration(lambda: psycopg.connect(**connection_kwargs()))
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
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
