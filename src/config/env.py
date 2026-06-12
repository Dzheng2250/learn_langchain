"""Small, typed environment-variable readers used by committed settings."""

import os


def env_str(name: str, default: str) -> str:
    """Read a string environment variable or return ``default``."""
    return os.getenv(name, default)


def env_int(name: str, default: int) -> int:
    """Read an integer environment variable with a descriptive parse error."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def env_float(name: str, default: float) -> float:
    """Read a floating-point environment variable with validation."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def env_bool(name: str, default: bool) -> bool:
    """Read common boolean spellings such as true/false, yes/no, and 1/0."""
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value, got {raw!r}")
