"""Resolve and validate workspace roots without trusting the caller."""

import os
from pathlib import Path


def canonicalize_workspace(path: str | Path) -> Path:
    """Return a validated absolute workspace directory."""
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValueError("workspace_root must reference an existing directory") from exc
    if not resolved.is_dir():
        raise ValueError("workspace_root must reference an existing directory")
    return resolved


def canonical_workspace_identity(path: str | Path) -> tuple[Path, str]:
    """Resolve a Workspace once and return its path plus stable comparison key."""
    root = canonicalize_workspace(path)
    return root, os.path.normcase(str(root))


def canonical_path_key(path: str | Path) -> str:
    """Return the stable comparison key stored in PostgreSQL."""
    return canonical_workspace_identity(path)[1]


def discover_workspace_root(start: str | Path | None = None) -> Path:
    """Use the nearest Git root, falling back to the starting directory."""
    current = canonicalize_workspace(start or Path.cwd())
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def resolve_workspace_path(root: Path, relative_path: str | Path) -> Path:
    """Resolve a relative path and reject traversal or symlink escapes."""
    root = canonicalize_workspace(root)
    value = Path(relative_path)
    if value.is_absolute():
        raise ValueError("workspace paths must be relative")
    try:
        candidate = (root / value).resolve(strict=True)
    except OSError as exc:
        raise ValueError("workspace path does not exist or cannot be accessed") from exc
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes the workspace") from exc
    return candidate


def resolve_workspace_target(root: Path, relative_path: str | Path) -> Path:
    """Resolve an existing or new target without allowing Workspace escape."""
    root = canonicalize_workspace(root)
    value = Path(relative_path)
    if value.is_absolute():
        raise ValueError("workspace paths must be relative")
    candidate = root / value
    existing = candidate
    missing_parts = []
    while not existing.exists():
        if existing == root:
            break
        missing_parts.append(existing.name)
        existing = existing.parent
    resolved_parent = existing.resolve(strict=True)
    try:
        resolved_parent.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes the workspace") from exc
    target = resolved_parent.joinpath(*reversed(missing_parts))
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes the workspace") from exc
    return target
