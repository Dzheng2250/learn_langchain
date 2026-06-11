"""User-level filesystem locations shared by CLI and Core."""

import os
from pathlib import Path

from platformdirs import user_config_path, user_data_path, user_state_path


APP_NAME = "learn-agent"


def user_config_dir() -> Path:
    return user_config_path(APP_NAME, appauthor=False)


def user_state_dir() -> Path:
    return user_state_path(APP_NAME, appauthor=False)


def user_data_dir() -> Path:
    return user_data_path(APP_NAME, appauthor=False)


def runtime_dir() -> Path:
    override = os.getenv("LEARN_AGENT_RUNTIME_DIR")
    return Path(override).expanduser().resolve() if override else user_state_dir() / "runtime"


def env_file() -> Path:
    override = os.getenv("LEARN_AGENT_ENV_FILE")
    return Path(override).expanduser().resolve() if override else user_config_dir() / ".env"


def backup_dir() -> Path:
    return user_data_dir() / "backups"
