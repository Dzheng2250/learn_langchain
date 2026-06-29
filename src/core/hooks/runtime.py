"""Workspace-aware discovery and caching of lifecycle Hook dispatchers."""

from pathlib import Path
from threading import Lock

from src.core.hooks.config import build_hook_dispatcher


class HookRuntimeRegistry:
    def __init__(self, settings) -> None:
        self.settings = settings
        self._lock = Lock()
        self._dispatchers = {}

    def get(self, workspace_root: str | Path):
        root = Path(workspace_root).resolve()
        with self._lock:
            existing = self._dispatchers.get(root)
            if existing is not None:
                return existing
            files = list(self.settings.config_files)
            if self.settings.project_hooks_enabled:
                files.append(root / ".learn-agent" / "hooks.json")
            dispatcher = build_hook_dispatcher(files, enabled=self.settings.enabled)
            self._dispatchers[root] = dispatcher
            return dispatcher
