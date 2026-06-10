"""Shared local daemon credential and runtime-file helpers."""

import hmac
import os
import secrets
from pathlib import Path

from src.config.settings import CORE_RUNTIME_DIR


def runtime_dir(base_dir: str | Path | None = None) -> Path:
    return Path(base_dir or CORE_RUNTIME_DIR).resolve()


def token_path(base_dir: str | Path | None = None) -> Path:
    return runtime_dir(base_dir) / "daemon.token"


def pid_path(base_dir: str | Path | None = None) -> Path:
    return runtime_dir(base_dir) / "daemon.pid"


def log_path(base_dir: str | Path | None = None) -> Path:
    return runtime_dir(base_dir) / "daemon.log"


def ensure_runtime_dir(base_dir: str | Path | None = None) -> Path:
    path = runtime_dir(base_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_token(base_dir: str | Path | None = None) -> str:
    ensure_runtime_dir(base_dir)
    token = secrets.token_urlsafe(32)
    path = token_path(base_dir)
    path.write_text(token, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def read_token(base_dir: str | Path | None = None) -> str:
    return token_path(base_dir).read_text(encoding="utf-8").strip()


def verify_token(expected: str, received: str) -> bool:
    return hmac.compare_digest(expected, received)
