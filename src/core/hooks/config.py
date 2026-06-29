"""Load command lifecycle hooks from explicit JSON configuration files."""

import json
from pathlib import Path

from src.core.hooks.dispatcher import HookDispatcher
from src.core.hooks.handlers import CommandHook
from src.core.hooks.models import HookFailureMode, HookPoint, HookSpec
from src.core.hooks.registry import HookRegistry


def build_hook_dispatcher(config_files=(), *, enabled: bool = True) -> HookDispatcher:
    registry = HookRegistry()
    if enabled:
        for raw_path in config_files:
            path = Path(raw_path).expanduser().resolve()
            if not path.is_file():
                continue
            _register_file(registry, path)
    registry.freeze()
    return HookDispatcher(registry, enabled=enabled)


def _register_file(registry: HookRegistry, path: Path) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    hooks = document.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"Hook config {path} must contain an object named hooks")
    for point_name, groups in hooks.items():
        point = HookPoint(point_name)
        for group_index, group in enumerate(groups or []):
            matcher = str(group.get("matcher", "*"))
            for handler_index, item in enumerate(group.get("hooks", [])):
                if item.get("type", "command") != "command":
                    raise ValueError("Only command hooks are supported in this release")
                command = item.get("command")
                if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
                    raise ValueError("Command hooks must use an argv string array")
                registry.register(HookSpec(
                    hook_id=str(item.get("id") or f"{path.name}:{point.value}:{group_index}:{handler_index}"),
                    point=point,
                    handler=CommandHook(tuple(command), float(item.get("timeout", 30))),
                    matcher=matcher,
                    priority=int(item.get("priority", 100)),
                    failure_mode=HookFailureMode(item.get("failure_mode", "open")),
                ))
