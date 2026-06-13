"""Validated runtime configuration for the Core daemon."""

import ipaddress
from dataclasses import dataclass
from pathlib import Path

from src.config import settings
from src.config.paths import runtime_dir


@dataclass(frozen=True)
class CoreConfig:
    """Configuration required to compose and run Core."""

    host: str
    port: int
    max_message_bytes: int
    shutdown_timeout_seconds: float
    runtime_dir: Path
    manage_runtime_files: bool = True

    @classmethod
    def load(
        cls,
        *,
        host: str | None = None,
        port: int | None = None,
        manage_runtime_files: bool = True,
    ) -> "CoreConfig":
        """Build and validate daemon configuration with optional host/port overrides."""
        config = cls(
            host=host if host is not None else settings.CORE_HOST,
            port=port if port is not None else settings.CORE_PORT,
            max_message_bytes=settings.CORE_MAX_MESSAGE_BYTES,
            shutdown_timeout_seconds=settings.CORE_SHUTDOWN_TIMEOUT_SECONDS,
            runtime_dir=runtime_dir().resolve(),
            manage_runtime_files=manage_runtime_files,
        )
        config.validate()
        return config

    def validate(self) -> None:
        """Reject unsafe network exposure and invalid transport limits."""
        try:
            address = ipaddress.ip_address(self.host)
        except ValueError as exc:
            if self.host != "localhost":
                raise ValueError("Core host must be a loopback address or localhost") from exc
        else:
            if not address.is_loopback:
                raise ValueError("Core host must be a loopback address")
        if not 0 <= self.port <= 65535:
            raise ValueError("Core port must be between 0 and 65535")
        if self.max_message_bytes <= 0:
            raise ValueError("Core max message size must be greater than zero")
        if self.shutdown_timeout_seconds <= 0:
            raise ValueError("Core shutdown timeout must be greater than zero")
