"""PostgreSQL backup helpers used before destructive migrations."""

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from src.config.paths import backup_dir
from src.config.settings import PG_DUMP_PATH, POSTGRES_DOCKER_CONTAINER
from src.core.database.connection import connection_kwargs


def create_database_backup() -> Path:
    """Create a complete custom-format dump before destructive migration."""
    target_dir = backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"learn_agent_{datetime.now():%Y%m%d_%H%M%S}.dump"
    config = connection_kwargs()
    host = str(config.get("host", "127.0.0.1"))
    port = str(config.get("port", 5432))
    user = str(config.get("user", "postgres"))
    dbname = str(config.get("dbname", "learn_agent"))
    password = str(config.get("password") or "")
    native = PG_DUMP_PATH or shutil.which("pg_dump")
    try:
        if native:
            _run_native_pg_dump(target, native, host, port, user, dbname, password)
        elif shutil.which("docker"):
            _run_docker_pg_dump(target, user, dbname, password)
        else:
            raise RuntimeError("No pg_dump executable or Docker fallback is available.")
    except subprocess.CalledProcessError as exc:
        target.unlink(missing_ok=True)
        details = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Database backup command failed: {details or exc}") from exc
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError("Database backup failed or produced an empty file.")
    return target


def _run_native_pg_dump(
    target: Path,
    native: str,
    host: str,
    port: str,
    user: str,
    dbname: str,
    password: str,
) -> None:
    command = [
        native,
        "-Fc",
        "-h",
        host,
        "-p",
        port,
        "-U",
        user,
        "-d",
        dbname,
        "-f",
        str(target),
    ]
    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password
    subprocess.run(command, check=True, env=env, capture_output=True)


def _run_docker_pg_dump(target: Path, user: str, dbname: str, password: str) -> None:
    docker_command = ["docker", "exec"]
    if password:
        docker_command.extend(["-e", f"PGPASSWORD={password}"])
    docker_command.extend(
        [
            POSTGRES_DOCKER_CONTAINER,
            "pg_dump",
            "-Fc",
            "-U",
            user,
            "-d",
            dbname,
        ]
    )
    with target.open("wb") as output:
        subprocess.run(
            docker_command,
            check=True,
            stdout=output,
            stderr=subprocess.PIPE,
        )
