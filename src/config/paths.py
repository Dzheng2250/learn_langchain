"""User-level filesystem locations shared by CLI and Core."""

import os
from pathlib import Path

from platformdirs import user_config_path, user_data_path, user_state_path


APP_NAME = "learn-agent"


def user_config_dir() -> Path:
    """Return the platform-specific directory for user configuration."""
    return user_config_path(APP_NAME, appauthor=False)


def user_state_dir() -> Path:
    """Return the platform-specific directory for mutable runtime state."""
    return user_state_path(APP_NAME, appauthor=False)


def user_data_dir() -> Path:
    """Return the platform-specific directory for durable application data."""
    return user_data_path(APP_NAME, appauthor=False)


def runtime_dir() -> Path:
    """Return daemon runtime storage, honoring ``LEARN_AGENT_RUNTIME_DIR``."""
    override = os.getenv("LEARN_AGENT_RUNTIME_DIR")
    return Path(override).expanduser().resolve() if override else user_state_dir() / "runtime"


def env_file() -> Path:
    """Return the user-level env file, honoring ``LEARN_AGENT_ENV_FILE``."""
    override = os.getenv("LEARN_AGENT_ENV_FILE")
    return Path(override).expanduser().resolve() if override else user_config_dir() / ".env"


def backup_dir() -> Path:
    """Return the default directory for database migration backups."""
    return user_data_dir() / "backups"


def local_state_dir() -> Path:
    """Return durable local-first state storage, honoring an explicit override."""
    override = os.getenv("LEARN_AGENT_STATE_DIR")
    return Path(override).expanduser().resolve() if override else user_data_dir() / "state"


def local_state_db() -> Path:
    """Return the authoritative SQLite business-state database path."""
    return local_state_dir() / "state.db"


def checkpoint_db() -> Path:
    """Return the LangGraph execution-checkpoint SQLite database path."""
    return local_state_dir() / "checkpoints.db"


def artifact_dir() -> Path:
    """Return the content-addressed directory used for large durable payloads."""
    return local_state_dir() / "artifacts"


def telemetry_dir() -> Path:
    """Return the default local telemetry directory."""
    return local_state_dir() / "telemetry"


def trace_dir() -> Path:
    """Return system-trace storage, honoring an explicit override."""
    override = os.getenv("LEARN_AGENT_TRACE_DIR")
    return Path(override).expanduser().resolve() if override else local_state_dir() / "traces"
