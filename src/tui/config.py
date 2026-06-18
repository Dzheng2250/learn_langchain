"""TUI configuration — host, port, runtime directory, session defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.config.settings import CORE_HOST, CORE_PORT
from src.config.paths import runtime_dir as default_runtime_dir


@dataclass(frozen=True)
class TuiConfig:
    """Configuration consumed by the TUI client."""

    core_host: str = CORE_HOST
    core_port: int = CORE_PORT
    connect_timeout: float = 10.0
    request_timeout: float = 300.0
    runtime_dir: Path = field(default_factory=lambda: default_runtime_dir().resolve())
    default_session: str = "default"