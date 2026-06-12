"""CLI-side workspace discovery without depending on Core internals."""

from pathlib import Path


def discover_workspace_root(start: str | Path | None = None) -> Path:
    """Return the nearest Git root, or the resolved starting directory."""
    current = Path(start or Path.cwd()).expanduser().resolve(strict=True)
    if not current.is_dir():
        raise ValueError("workspace must be an existing directory")
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current
