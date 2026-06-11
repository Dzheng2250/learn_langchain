"""Explicit loading of Core process secrets."""

from pathlib import Path

from dotenv import load_dotenv

from src.config.paths import env_file


def load_core_environment(path: Path | None = None) -> Path:
    """Load the user-level secret file once without cwd-based discovery."""
    target = (path or env_file()).resolve()
    if target.exists():
        load_dotenv(target, override=False)
    return target
