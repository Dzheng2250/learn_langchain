"""Helpers shared by resource-aware Workspace tool adapters."""
import hashlib
from pathlib import Path
from urllib.parse import quote

from src.config.settings import RESOURCE_ACTIVITY_HASH_ENABLED
from src.core.workspace.resolver import canonicalize_workspace, resolve_workspace_target


def workspace_uri(root: Path, target: Path | str) -> str:
    """Return a canonical URI using the same path rules as Workspace mutations."""
    root = canonicalize_workspace(root)
    raw = str(target)
    if raw.startswith("workspace://"):
        raw = raw.removeprefix("workspace://")
    value = Path(raw)
    if value.is_absolute():
        try:
            value = value.relative_to(root)
        except ValueError as exc:
            raise ValueError("path escapes the workspace") from exc
    resolved = resolve_workspace_target(root, value)
    return "workspace://" + resolved.relative_to(root).as_posix()


def command_uri(executor: str, command: str) -> str:
    """Identify a command invocation without persisting its sensitive text."""
    digest = hashlib.sha256(command.encode("utf-8")).hexdigest()
    return f"command://{quote(executor, safe='')}/{digest}"


def file_snapshot(
    path: Path,
    *,
    data: bytes | None = None,
    hash_enabled: bool = RESOURCE_ACTIVITY_HASH_ENABLED,
) -> dict:
    """Return bounded metadata, reusing bytes already read by a tool when available."""
    if not path.exists():
        return {"bytes": 0, "lines": None, "digest": ""}
    if not path.is_file():
        return {
            "bytes": 0,
            "lines": None,
            "digest": "",
            "entries_recursive": sum(1 for _ in path.rglob("*")) if path.is_dir() else 0,
        }
    payload = path.read_bytes() if data is None else data
    return {
        "bytes": len(payload),
        "lines": len(payload.splitlines()),
        "digest": hashlib.sha256(payload).hexdigest() if hash_enabled else "",
    }