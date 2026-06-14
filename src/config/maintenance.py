"""Typed runtime policy for durable background maintenance."""

from dataclasses import dataclass

from src.config.env import env_float, env_int


@dataclass(frozen=True)
class MaintenanceSettings:
    """Tunable scheduling, leasing, retry, and shutdown behavior."""

    poll_interval_seconds: float = 0.25
    lease_seconds: float = 60.0
    shutdown_timeout_seconds: float = 5.0
    default_max_attempts: int = 5
    max_retry_delay_seconds: int = 300
    error_preview_limit: int = 2000

    @classmethod
    def load(cls) -> "MaintenanceSettings":
        """Load environment overrides and validate the resulting policy."""
        settings = cls(
            poll_interval_seconds=env_float(
                "LEARN_AGENT_MAINTENANCE_POLL_INTERVAL_SECONDS", 0.25
            ),
            lease_seconds=env_float("LEARN_AGENT_MAINTENANCE_LEASE_SECONDS", 60.0),
            shutdown_timeout_seconds=env_float(
                "LEARN_AGENT_MAINTENANCE_SHUTDOWN_TIMEOUT_SECONDS", 5.0
            ),
            default_max_attempts=env_int(
                "LEARN_AGENT_MAINTENANCE_MAX_ATTEMPTS", 5
            ),
            max_retry_delay_seconds=env_int(
                "LEARN_AGENT_MAINTENANCE_MAX_RETRY_DELAY_SECONDS", 300
            ),
            error_preview_limit=env_int(
                "LEARN_AGENT_MAINTENANCE_ERROR_PREVIEW_LIMIT", 2000
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        """Reject values that would disable progress or reliable shutdown."""
        if self.poll_interval_seconds <= 0:
            raise ValueError("Maintenance poll interval must be greater than zero")
        if self.lease_seconds <= 0:
            raise ValueError("Maintenance lease must be greater than zero")
        if self.shutdown_timeout_seconds < 0:
            raise ValueError("Maintenance shutdown timeout cannot be negative")
        if self.default_max_attempts <= 0:
            raise ValueError("Maintenance max attempts must be greater than zero")
        if self.max_retry_delay_seconds <= 0:
            raise ValueError("Maintenance retry delay must be greater than zero")
        if self.error_preview_limit <= 0:
            raise ValueError("Maintenance error preview limit must be greater than zero")
