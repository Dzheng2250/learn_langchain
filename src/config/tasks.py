"""Configuration for the Agent-private task planning tools."""

from dataclasses import dataclass

from src.config.env import env_int


@dataclass(frozen=True)
class TaskSettings:
    """Limits for one Execution's private task plan."""

    max_tasks_per_execution: int = 40
    task_key_max_chars: int = 64
    subject_max_chars: int = 160
    description_max_chars: int = 2000
    notes_max_chars: int = 4000
    list_output_limit: int = 8000

    @classmethod
    def load(cls) -> "TaskSettings":
        """Load task limits from environment-backed configuration."""
        settings = cls(
            max_tasks_per_execution=env_int("LEARN_AGENT_TASK_MAX_PER_EXECUTION", 40),
            task_key_max_chars=env_int("LEARN_AGENT_TASK_KEY_MAX_CHARS", 64),
            subject_max_chars=env_int("LEARN_AGENT_TASK_SUBJECT_MAX_CHARS", 160),
            description_max_chars=env_int("LEARN_AGENT_TASK_DESCRIPTION_MAX_CHARS", 2000),
            notes_max_chars=env_int("LEARN_AGENT_TASK_NOTES_MAX_CHARS", 4000),
            list_output_limit=env_int("LEARN_AGENT_TASK_LIST_OUTPUT_LIMIT", 8000),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        """Reject non-positive limits before tools are exposed to the Agent."""
        for name, value in self.__dict__.items():
            if int(value) <= 0:
                raise ValueError(f"{name} must be greater than zero")
