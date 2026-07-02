"""Workspace-bound mutation tools with atomic writes and strict path checks."""

import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable
from uuid import uuid4

from langchain_core.tools import tool

from src.core.telemetry import emit_event
from src.core.resource_activity import ChangeState, ObservationMode, ResourceObservation, ResourceOperation, record_resource_activity
from src.core.resource_activity.observation import file_snapshot, workspace_uri
from src.core.tools.workspace import is_workspace_path_blocked
from src.core.workspace.resolver import canonicalize_workspace, resolve_workspace_target


def _safe_target(root: Path, path: str) -> Path:
    target = resolve_workspace_target(root, path)
    if target == root:
        raise ValueError("workspace root cannot be mutated")
    if is_workspace_path_blocked(root, target):
        raise ValueError("path is blocked by workspace policy")
    return target


def _check_size(content: str, max_bytes: int) -> int:
    size = len(content.encode("utf-8"))
    if size > max_bytes:
        raise ValueError(f"content exceeds the {max_bytes}-byte write limit")
    return size


def _atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _record_change(root: Path, target: Path, operation: ResourceOperation, before: dict, after: dict, **metadata) -> None:
    record_resource_activity(ResourceObservation(
        workspace_uri(root, target), operation, ObservationMode.EXACT,
        change_state=ChangeState.APPLIED, resource_bytes=after.get("bytes", 0),
        before_digest=before.get("digest", ""), after_digest=after.get("digest", ""),
        before_lines=before.get("lines"), after_lines=after.get("lines"),
        metadata={
            **metadata,
            "before_entries_recursive": before.get("entries_recursive"),
            "after_entries_recursive": after.get("entries_recursive"),
        },
    ))

def _observed_write(operation: str, path: str, action: Callable[[], str]) -> str:
    emit_event(
        "workspace_write_requested", "workspace_tools", "Workspace write requested.",
        {"operation": operation, "path": path},
    )
    try:
        result = action()
    except Exception as exc:
        emit_event(
            "workspace_write_rejected", "workspace_tools", "Workspace write rejected.",
            {"operation": operation, "path": path, "error_type": type(exc).__name__},
            level="error",
        )
        raise
    emit_event(
        "workspace_write_completed", "workspace_tools", "Workspace write completed.",
        {"operation": operation, "path": path},
    )
    return result


def create_workspace_write_tools(root: Path, *, max_bytes: int, max_entries: int = 100) -> tuple:
    """Create Parent-only mutation tools bound to one canonical Workspace."""
    root = canonicalize_workspace(root)
    if max_bytes <= 0 or max_entries <= 0:
        raise ValueError("Workspace write limits must be greater than zero")

    def ensure_entry_limit(target: Path) -> None:
        if target.is_dir():
            count = sum(1 for _ in target.rglob("*"))
            if count > max_entries:
                raise ValueError(
                    f"operation affects {count} entries; limit is {max_entries}"
                )

    @tool
    def write_workspace_file(path: str, content: str, overwrite: bool = False) -> str:
        """Create a UTF-8 text file, or atomically overwrite it when explicitly allowed."""
        def action() -> str:
            target = _safe_target(root, path)
            if target.exists() and not target.is_file():
                raise ValueError("target exists and is not a regular file")
            if target.exists() and not overwrite:
                raise FileExistsError("target exists; set overwrite=true to replace it")
            size = _check_size(content, max_bytes)
            before = file_snapshot(target)
            _atomic_write(target, content)
            after = file_snapshot(target)
            _record_change(root, target, ResourceOperation.WRITE if before["digest"] else ResourceOperation.CREATE, before, after)
            return f"Wrote {size} bytes to {path}."
        return _observed_write("write", path, action)

    @tool
    def replace_workspace_text(
        path: str,
        old_text: str,
        new_text: str,
        expected_count: int = 1,
    ) -> str:
        """Atomically replace exact text only when its occurrence count matches."""
        def action() -> str:
            if expected_count <= 0:
                raise ValueError("expected_count must be greater than zero")
            target = _safe_target(root, path)
            if not target.is_file():
                raise ValueError("target is not a regular file")
            before = file_snapshot(target)
            original = target.read_text(encoding="utf-8")
            actual = original.count(old_text)
            if actual != expected_count:
                raise ValueError(
                    f"expected {expected_count} occurrences but found {actual}; file was not changed"
                )
            updated = original.replace(old_text, new_text)
            size = _check_size(updated, max_bytes)
            _atomic_write(target, updated)
            _record_change(root, target, ResourceOperation.WRITE, before, file_snapshot(target), replacements=actual)
            return f"Replaced {actual} occurrence(s) in {path}; file is {size} bytes."
        return _observed_write("replace", path, action)

    @tool
    def create_workspace_directory(path: str, parents: bool = True) -> str:
        """Create a directory inside the Workspace without replacing an existing file."""
        def action() -> str:
            target = _safe_target(root, path)
            if target.exists():
                if target.is_dir():
                    return f"Directory already exists: {path}."
                raise FileExistsError("target exists and is not a directory")
            target.mkdir(parents=parents, exist_ok=False)
            _record_change(root, target, ResourceOperation.CREATE, {"bytes": 0, "lines": None, "digest": ""}, file_snapshot(target), resource_type="directory")
            return f"Created directory {path}."
        return _observed_write("mkdir", path, action)

    @tool
    def move_workspace_path(source: str, destination: str, overwrite: bool = False) -> str:
        """Move one Workspace file or directory; replacement must be explicit."""
        def action() -> str:
            source_target = _safe_target(root, source)
            destination_target = _safe_target(root, destination)
            if not source_target.exists():
                raise FileNotFoundError("source does not exist")
            source_before = file_snapshot(source_target)
            destination_before = file_snapshot(destination_target)
            ensure_entry_limit(source_target)
            if source_target.is_dir() and destination_target.is_relative_to(source_target):
                raise ValueError("cannot move a directory into itself")
            if destination_target.exists():
                if not overwrite:
                    raise FileExistsError("destination exists; set overwrite=true to replace it")
                if destination_target.is_dir():
                    shutil.rmtree(destination_target)
                else:
                    destination_target.unlink()
            if not destination_target.parent.is_dir():
                raise ValueError("destination parent directory does not exist")
            shutil.move(str(source_target), str(destination_target))
            destination_after = file_snapshot(destination_target)
            change_group_id = uuid4().hex
            source_uri = workspace_uri(root, source_target)
            destination_uri = workspace_uri(root, destination_target)
            _record_change(
                root, source_target, ResourceOperation.MOVE, source_before,
                {"bytes": 0, "lines": None, "digest": ""},
                move_role="source", change_group_id=change_group_id, destination_uri=destination_uri,
            )
            _record_change(
                root, destination_target, ResourceOperation.MOVE, destination_before,
                destination_after, move_role="destination", change_group_id=change_group_id, source_uri=source_uri,
            )
            return f"Moved {source} to {destination}."
        return _observed_write("move", f"{source} -> {destination}", action)

    @tool
    def delete_workspace_path(path: str, recursive: bool = False) -> str:
        """Delete a file or an explicitly recursive directory inside the Workspace."""
        def action() -> str:
            target = _safe_target(root, path)
            if not target.exists():
                raise FileNotFoundError("target does not exist")
            before = file_snapshot(target)
            if target.is_dir():
                ensure_entry_limit(target)
                if not recursive:
                    target.rmdir()
                else:
                    shutil.rmtree(target)
            else:
                target.unlink()
            _record_change(root, target, ResourceOperation.DELETE, before, {"bytes": 0, "lines": None, "digest": ""})
            return f"Deleted {path}."
        return _observed_write("delete", path, action)

    return (
        write_workspace_file,
        replace_workspace_text,
        create_workspace_directory,
        move_workspace_path,
        delete_workspace_path,
    )
