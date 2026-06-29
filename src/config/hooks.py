"""Typed configuration for the system-level lifecycle Hook runtime."""

import os
from dataclasses import dataclass
from pathlib import Path

from src.config.env import env_bool, env_str
from src.config.paths import user_config_dir


@dataclass(frozen=True)
class HookSettings:
    enabled: bool
    config_files: tuple[Path, ...]
    project_hooks_enabled: bool

    @classmethod
    def load(cls) -> "HookSettings":
        configured = env_str("LEARN_AGENT_HOOK_CONFIG_FILES", "").strip()
        paths = [user_config_dir() / "hooks.json"]
        if configured:
            paths.extend(Path(item).expanduser() for item in configured.split(os.pathsep) if item)
        return cls(
            enabled=env_bool("LEARN_AGENT_HOOKS_ENABLED", True),
            config_files=tuple(path.resolve() for path in paths),
            project_hooks_enabled=env_bool("LEARN_AGENT_PROJECT_HOOKS_ENABLED", False),
        )
