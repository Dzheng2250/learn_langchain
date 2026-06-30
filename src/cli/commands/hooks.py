"""Inspect and initialize Agent lifecycle Hook configuration."""

import json
from pathlib import Path

from src.cli.workspace import discover_workspace_root
from src.config.hooks import HookSettings
from src.core.hooks.config import build_hook_dispatcher


def register(subparsers, _config) -> None:
    """Register Hook discovery and initialization commands."""
    parser = subparsers.add_parser("hooks", help="inspect or initialize lifecycle hooks")
    actions = parser.add_subparsers(dest="hooks_action", required=True)

    path = actions.add_parser("path", help="show hook configuration files Core will read")
    path.add_argument("--workspace", help="include the project hook path for this Workspace")
    path.set_defaults(handler=run)

    init = actions.add_parser("init", help="create a hooks.json template")
    init.add_argument("--path", help="write the template to this explicit path")
    init.add_argument("--project", action="store_true", help="write .learn-agent/hooks.json in the Workspace")
    init.add_argument("--workspace", help="Workspace used with --project")
    init.add_argument("--force", action="store_true", help="overwrite an existing file")
    init.set_defaults(handler=run)

    validate = actions.add_parser("validate", help="parse configured hook files")
    validate.add_argument("--workspace", help="include the project hook path for this Workspace")
    validate.set_defaults(handler=run)


def run(args, _config) -> int:
    """Execute one hooks command without contacting Core."""
    settings = HookSettings.load()
    if args.hooks_action == "path":
        print(f"hooks_enabled={settings.enabled}")
        print(f"project_hooks_enabled={settings.project_hooks_enabled}")
        for path in _config_files(settings, getattr(args, "workspace", None)):
            state = "exists" if path.is_file() else "missing"
            print(f"{path} ({state})")
        return 0

    if args.hooks_action == "init":
        if args.path and args.project:
            raise RuntimeError("--path and --project cannot be used together.")
        if args.project:
            target = discover_workspace_root(args.workspace) / ".learn-agent" / "hooks.json"
        else:
            target = Path(args.path).expanduser().resolve() if args.path else settings.config_files[0]
        if target.exists() and not args.force:
            raise RuntimeError(f"Refusing to overwrite existing hook config: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(_template_document(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Initialized hook config: {target}")
        return 0

    if args.hooks_action == "validate":
        files = _config_files(settings, getattr(args, "workspace", None))
        build_hook_dispatcher(files, enabled=settings.enabled)
        existing = [str(path) for path in files if path.is_file()]
        if existing:
            print("Hook config is valid:")
            for path in existing:
                print(f"- {path}")
        else:
            print("No hook config files found.")
        return 0

    raise RuntimeError(f"Unsupported hooks action: {args.hooks_action}")


def _config_files(settings: HookSettings, workspace: str | None = None) -> tuple[Path, ...]:
    files = list(settings.config_files)
    if workspace or settings.project_hooks_enabled:
        root = discover_workspace_root(workspace)
        files.append(root / ".learn-agent" / "hooks.json")
    return tuple(files)


def _template_document() -> dict:
    """Return a safe template that documents examples without enabling hooks."""
    return {
        "_description": (
            "learn-agent lifecycle hook configuration. Keep hooks empty until "
            "you have created and reviewed the referenced command scripts."
        ),
        "hooks": {},
        "_examples": {
            "PreToolUse": [
                {
                    "matcher": "^run_command_in_container$",
                    "hooks": [
                        {
                            "id": "block-dangerous-command",
                            "type": "command",
                            "command": ["python", "D:/my-hooks/block_danger.py"],
                            "timeout": 2,
                            "priority": 10,
                            "failure_mode": "closed",
                        }
                    ],
                }
            ],
            "PermissionRequest": [
                {
                    "matcher": "^run_command_in_container$",
                    "hooks": [
                        {
                            "id": "auto-approve-low-risk",
                            "type": "command",
                            "command": ["python", "D:/my-hooks/approval_agent.py"],
                            "timeout": 5,
                            "priority": 20,
                            "failure_mode": "open",
                        }
                    ],
                }
            ],
        },
    }