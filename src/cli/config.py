"""Validated configuration used by the CLI process."""

import ipaddress
from dataclasses import dataclass
from pathlib import Path

from src.config import settings
from src.config.paths import runtime_dir


@dataclass(frozen=True)
class CliConfig:
    """Configuration required by the CLI and daemon lifecycle commands."""

    core_host: str
    core_port: int
    connect_timeout_seconds: float
    runtime_dir: Path
    default_session_id: str

    @classmethod
    def load(cls) -> "CliConfig":
        """Read committed settings and validate values before CLI dispatch."""
        config = cls(
            core_host=settings.CORE_HOST,
            core_port=settings.CORE_PORT,
            connect_timeout_seconds=settings.CORE_CONNECT_TIMEOUT_SECONDS,
            runtime_dir=runtime_dir().resolve(),
            default_session_id=settings.DEFAULT_SESSION_ID,
        )
        config.validate()
        return config

    def validate(self) -> None:
        """Reject unsafe or unusable CLI transport settings."""
        try:
            address = ipaddress.ip_address(self.core_host)
        except ValueError as exc:
            if self.core_host != "localhost":
                raise ValueError("CORE_HOST must be a loopback address or localhost") from exc
        else:
            if not address.is_loopback:
                raise ValueError("CORE_HOST must be a loopback address")

        if not 1 <= self.core_port <= 65535:
            raise ValueError("CORE_PORT must be between 1 and 65535")
        if self.connect_timeout_seconds <= 0:
            raise ValueError("CORE_CONNECT_TIMEOUT_SECONDS must be greater than zero")
        if not self.default_session_id.strip():
            raise ValueError("DEFAULT_SESSION_ID must not be empty")
