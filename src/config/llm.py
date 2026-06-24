"""Typed resilience policy for all model workloads."""

from dataclasses import dataclass

from src.config.env import env_bool, env_float, env_int


@dataclass(frozen=True)
class LlmRetrySettings:
    """Bound retries so provider failures cannot stall a Core worker indefinitely."""

    enabled: bool = True
    foreground_max_attempts: int = 3
    background_max_attempts: int = 2
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    jitter_ratio: float = 0.1

    @classmethod
    def load(cls) -> "LlmRetrySettings":
        settings = cls(
            enabled=env_bool("LEARN_AGENT_LLM_RETRY_ENABLED", True),
            foreground_max_attempts=env_int("LEARN_AGENT_LLM_FOREGROUND_MAX_ATTEMPTS", 3),
            background_max_attempts=env_int("LEARN_AGENT_LLM_BACKGROUND_MAX_ATTEMPTS", 2),
            base_delay_seconds=env_float("LEARN_AGENT_LLM_RETRY_BASE_DELAY_SECONDS", 1.0),
            max_delay_seconds=env_float("LEARN_AGENT_LLM_RETRY_MAX_DELAY_SECONDS", 30.0),
            jitter_ratio=env_float("LEARN_AGENT_LLM_RETRY_JITTER_RATIO", 0.1),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.foreground_max_attempts <= 0 or self.background_max_attempts <= 0:
            raise ValueError("LLM retry attempts must be greater than zero")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("LLM retry delays cannot be negative")
        if self.base_delay_seconds > self.max_delay_seconds:
            raise ValueError("LLM retry base delay cannot exceed max delay")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("LLM retry jitter ratio must be between zero and one")
