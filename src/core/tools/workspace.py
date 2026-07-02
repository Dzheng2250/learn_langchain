"""Workspace-bound file tools."""

from pathlib import Path

from langchain_core.tools import tool

from src.config.settings import (
    ENTIRE_FILE_MAX_LINES,
    FILE_READ_CHUNK_LINES,
    FILE_READ_OUTPUT_LIMIT,
    PARENT_FILE_READ_LINES,
    PARENT_FILE_READ_OUTPUT_LIMIT,
)
from src.core.workspace.resolver import resolve_workspace_path
from src.core.resource_activity import ObservationMode, ResourceObservation, ResourceOperation, record_resource_activity
from src.core.resource_activity.observation import file_snapshot, workspace_uri


SANDBOX_EXCLUDES = {
    ".env",
    ".git",
    ".vscode",
    "__pycache__",
    ".ipynb_checkpoints",
    ".agent_runtime",
    ".codegraph",
    "state.db",
    "checkpoints.db",
    "events.db",
}


def is_sandbox_name_excluded(name: str) -> bool:
    """Return whether one path component is blocked from Agent tools."""
    return name in SANDBOX_EXCLUDES or name.startswith(".env") or name.endswith(".pyc")


def is_workspace_path_blocked(root: Path, target: Path) -> bool:
    """Apply the shared sensitive-path policy to a resolved workspace path."""
    return any(is_sandbox_name_excluded(part) for part in target.relative_to(root).parts)


def read_workspace_lines(root: Path, path: str) -> tuple[Path, list[str], bytes]:
    """Resolve and read a text file after applying the shared path policy."""
    target = resolve_workspace_path(root, path)
    if not target.is_file():
        raise ValueError("target is not a file")
    if is_workspace_path_blocked(root, target):
        raise ValueError("path is blocked by workspace policy")
    data = target.read_bytes()
    return target, data.decode("utf-8", errors="replace").splitlines(), data


def _format_range(
    path: str,
    lines: list[str],
    start_line: int,
    max_lines: int,
    output_limit: int,
) -> tuple[str, dict[str, int] | None]:
    """Format a bounded range and return its exact observed coordinates."""
    start = max(1, int(start_line))
    limit = max(1, int(max_lines))
    selected = lines[start - 1:start - 1 + limit]
    if not selected:
        return f"{path} has {len(lines)} lines; start_line {start} is out of range.", None
    output = []
    chars = 0
    for number, line in enumerate(selected, start=start):
        value = f"{number}: {line}"
        if output and chars + len(value) + 1 > output_limit:
            break
        output.append(value[:output_limit])
        chars += len(value) + 1
    end = start + len(output) - 1
    return (
        f"{path}: lines {start}-{end} of {len(lines)}\n" + "\n".join(output),
        {"start_line": start, "end_line": end},
    )


def _read_mode(observed: dict[str, int] | None, total_lines: int) -> ObservationMode:
    if observed and observed["start_line"] == 1 and observed["end_line"] == total_lines:
        return ObservationMode.EXACT
    return ObservationMode.RANGE


def create_workspace_file_tools(root: Path) -> tuple:
    """Create file tools permanently bound to one workspace root."""

    @tool
    def read_workspace_file(path: str, start_line: int = 1, max_lines: int = FILE_READ_CHUNK_LINES) -> str:
        """Read a bounded known line range from a workspace file."""
        try:
            target, lines, data = read_workspace_lines(root, path)
            result, observed = _format_range(
                path, lines, start_line, min(max_lines, FILE_READ_CHUNK_LINES), FILE_READ_OUTPUT_LIMIT
            )
            snapshot = file_snapshot(target, data=data)
            record_resource_activity(ResourceObservation(
                workspace_uri(root, target), ResourceOperation.READ, _read_mode(observed, len(lines)),
                requested_range={"start_line": start_line, "max_lines": max_lines}, observed_range=observed,
                returned_bytes=len(result.encode("utf-8")), resource_bytes=snapshot["bytes"],
                after_digest=snapshot["digest"], after_lines=snapshot["lines"],
                metadata={"truncated": bool(observed and observed["end_line"] < len(lines))},
            ))
            return result
        except (OSError, ValueError) as exc:
            return f"Workspace file read rejected: {exc}"

    @tool
    def read_entire_file(path: str) -> str:
        """Read an entire small workspace file, capped by configured line limits."""
        try:
            target, lines, data = read_workspace_lines(root, path)
            selected = lines[:ENTIRE_FILE_MAX_LINES]
            result = f"{path}: {len(lines)} total lines\n" + "\n".join(
                f"{index}: {line}" for index, line in enumerate(selected, start=1)
            )
            if len(lines) > len(selected):
                result += f"\n... {len(lines) - len(selected)} lines omitted; use summarize_large_file."
            snapshot = file_snapshot(target, data=data)
            mode = ObservationMode.EXACT if len(selected) == len(lines) else ObservationMode.RANGE
            record_resource_activity(ResourceObservation(
                workspace_uri(root, target), ResourceOperation.READ, mode,
                observed_range={"start_line": 1, "end_line": len(selected)},
                returned_bytes=len(result.encode("utf-8")), resource_bytes=snapshot["bytes"],
                after_digest=snapshot["digest"], after_lines=snapshot["lines"],
                metadata={"truncated": len(selected) < len(lines)},
            ))
            return result
        except (OSError, ValueError) as exc:
            return f"Workspace file read rejected: {exc}"

    @tool
    def read_workspace_file_lite(
        path: str,
        start_line: int = 1,
        max_lines: int = PARENT_FILE_READ_LINES,
    ) -> str:
        """Read a small targeted snippet; delegate broad file inspection."""
        try:
            target, lines, data = read_workspace_lines(root, path)
            result, observed = _format_range(
                path, lines, start_line, min(max_lines, PARENT_FILE_READ_LINES), PARENT_FILE_READ_OUTPUT_LIMIT
            )
            snapshot = file_snapshot(target, data=data)
            record_resource_activity(ResourceObservation(
                workspace_uri(root, target), ResourceOperation.READ, _read_mode(observed, len(lines)),
                requested_range={"start_line": start_line, "max_lines": max_lines}, observed_range=observed,
                returned_bytes=len(result.encode("utf-8")), resource_bytes=snapshot["bytes"],
                after_digest=snapshot["digest"], after_lines=snapshot["lines"],
                metadata={"truncated": bool(observed and observed["end_line"] < len(lines))},
            ))
            return result
        except (OSError, ValueError) as exc:
            return f"Workspace file read rejected: {exc}"

    return read_workspace_file, read_entire_file, read_workspace_file_lite
