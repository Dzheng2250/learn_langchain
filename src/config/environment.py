"""Explicit loading of the shared user-level configuration file."""

from pathlib import Path

from dotenv import load_dotenv

from src.config.paths import env_file


def load_user_environment(path: Path | None = None) -> Path:
    """Load user configuration without overriding process environment values."""
    target = (path or env_file()).resolve()
    if target.exists():
        load_dotenv(target, override=False)
    return target
