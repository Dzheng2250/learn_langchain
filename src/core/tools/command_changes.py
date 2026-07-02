"""Two-phase command mutation tools backed by isolated Workspace snapshots."""

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from langchain_core.tools import tool

from src.config.paths import runtime_dir
from src.config.settings import (
    DOCKER_CPUS, DOCKER_IMAGE, DOCKER_MEMORY, DOCKER_OUTPUT_LIMIT,
    DOCKER_TIMEOUT_SECONDS,
)
from src.core.telemetry import emit_event
from src.core.tools.commands import _copy_workspace
from src.core.tools.workspace import is_workspace_path_blocked
from src.core.tools.workspace_write import _safe_target
from src.core.resource_activity import ChangeState, ObservationMode, ResourceObservation, ResourceOperation, record_resource_activity
from src.core.resource_activity.observation import workspace_uri
from src.core.workspace.resolver import canonicalize_workspace

_CHANGESET_TTL_HOURS = 24


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest(root: Path) -> dict[str, dict]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file() or is_workspace_path_blocked(root, path):
            continue
        relative = path.relative_to(root).as_posix()
        result[relative] = {"sha256": _sha256(path), "size": path.stat().st_size}
    return result


def _change_labels(changes: list[dict]) -> list[str]:
    return [f"{item['operation']}:{item['path']}" for item in changes]


def _changes(before: dict, after: dict) -> list[dict]:
    changes = []
    for path in sorted(before.keys() | after.keys()):
        if path not in before:
            changes.append({"path": path, "operation": "create", "size": after[path]["size"]})
        elif path not in after:
            changes.append({"path": path, "operation": "delete", "size": 0})
        elif before[path]["sha256"] != after[path]["sha256"]:
            changes.append({"path": path, "operation": "modify", "size": after[path]["size"]})
    return changes


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _workspace_key(root: Path) -> str:
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:20]


def _store_root(root: Path) -> Path:
    return runtime_dir() / "command_changesets" / _workspace_key(root)


def _make_writable(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        try:
            path.chmod(0o777 if path.is_dir() else 0o666)
        except OSError:
            pass
    try:
        root.chmod(0o777)
    except OSError:
        pass


def _load_changeset(root: Path, change_set_id: str) -> tuple[Path, dict]:
    if not change_set_id or any(char not in "0123456789abcdef" for char in change_set_id):
        raise ValueError("invalid change_set_id")
    directory = _store_root(root) / change_set_id
    metadata_path = directory / "metadata.json"
    if not metadata_path.is_file():
        raise FileNotFoundError("change set does not exist or has expired")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    expires_at = datetime.fromisoformat(metadata["expires_at"])
    if expires_at <= datetime.now(timezone.utc):
        shutil.rmtree(directory, ignore_errors=True)
        raise ValueError("change set has expired")
    return directory, metadata


def create_staged_command_tools(
    root: Path,
    *,
    max_files: int,
    max_bytes: int,
) -> tuple:
    """Create tools that stage container changes and apply them only after approval."""
    root = canonicalize_workspace(root)
    if max_files <= 0 or max_bytes <= 0:
        raise ValueError("Command change-set limits must be greater than zero")

    @tool
    def stage_command_changes(command: str) -> str:
        """Run a command in an isolated writable copy and stage its file changes."""
        command = command.strip()
        if not command:
            raise ValueError("command must not be empty")
        change_set_id = uuid4().hex
        directory = _store_root(root) / change_set_id
        work = directory / "workspace"
        work.mkdir(parents=True, exist_ok=False)
        try:
            _copy_workspace(root, work)
            before = _manifest(work)
            _make_writable(work)
            result = subprocess.run(
                [
                    "docker", "run", "--rm", "--network", "none",
                    "--cpus", DOCKER_CPUS, "--memory", DOCKER_MEMORY,
                    "--pids-limit", "128", "--read-only", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "--user", "65534:65534",
                    "--workdir", "/workspace",
                    "--mount", f"type=bind,source={work},target=/workspace",
                    "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
                    DOCKER_IMAGE, "bash", "-lc", command,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=DOCKER_TIMEOUT_SECONDS,
                check=False,
            )
            after = _manifest(work)
            changes = _changes(before, after)
            changed_bytes = sum(item["size"] for item in changes)
            if len(changes) > max_files:
                raise ValueError(f"command changed {len(changes)} files; limit is {max_files}")
            if changed_bytes > max_bytes:
                raise ValueError(f"command produced {changed_bytes} bytes; limit is {max_bytes}")
            now = datetime.now(timezone.utc)
            metadata = {
                "change_set_id": change_set_id,
                "workspace": str(root),
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=_CHANGESET_TTL_HOURS)).isoformat(),
                "before": before,
                "after": after,
                "changes": changes,
                "exit_code": result.returncode,
            }
            (directory / "metadata.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        emit_event(
            "command_changes_staged", "workspace_tools", "Command changes staged.",
            {"change_set_id": change_set_id, "file_count": len(changes), "bytes": changed_bytes},
        )
        for change in changes:
            operation = {"create": ResourceOperation.CREATE, "modify": ResourceOperation.WRITE, "delete": ResourceOperation.DELETE}[change["operation"]]
            record_resource_activity(ResourceObservation(
                workspace_uri(root, change["path"]), operation, ObservationMode.EXACT,
                change_state=ChangeState.PROPOSED, resource_bytes=change["size"],
                metadata={"change_set_id": change_set_id},
            ))
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        summary = json.dumps(changes, ensure_ascii=False)
        return (
            f"Staged change set {change_set_id}; exit_code={result.returncode}; "
            f"files={len(changes)}; bytes={changed_bytes}.\nChanges: {summary}\n"
            f"Approval fingerprint: {json.dumps(_change_labels(changes), ensure_ascii=False)}\n"
            f"Output: {(output or '(no output)')[:DOCKER_OUTPUT_LIMIT]}"
        )

    @tool
    def apply_staged_changes(change_set_id: str, expected_changes: list[str]) -> str:
        """Apply a staged change set only when its displayed fingerprint matches."""
        directory, metadata = _load_changeset(root, change_set_id)
        work = directory / "workspace"
        if metadata.get("workspace") != str(root):
            raise PermissionError("change set belongs to another Workspace")
        changes = metadata["changes"]
        if expected_changes != _change_labels(changes):
            raise PermissionError("expected_changes does not match the staged change set")
        before = metadata["before"]
        after = metadata["after"]
        targets = []
        for change in changes:
            path = change["path"]
            target = _safe_target(root, path)
            prior = before.get(path)
            if prior is None:
                if target.exists():
                    raise RuntimeError(f"Workspace changed since staging: {path} now exists")
            elif not target.is_file() or _sha256(target) != prior["sha256"]:
                raise RuntimeError(f"Workspace changed since staging: {path}")
            source = work / Path(path)
            if change["operation"] != "delete":
                expected = after.get(path)
                if (
                    expected is None
                    or source.is_symlink()
                    or not source.is_file()
                    or is_workspace_path_blocked(work, source)
                    or source.stat().st_size != expected["size"]
                    or _sha256(source) != expected["sha256"]
                ):
                    raise ValueError(f"staged source is invalid or changed: {path}")
            targets.append((change, target, source))

        backup = directory / "backup"
        backup.mkdir()
        created = []
        try:
            for change, target, source in targets:
                relative = Path(change["path"])
                if target.exists():
                    backup_target = backup / relative
                    backup_target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup_target)
                else:
                    created.append(target)
                if change["operation"] == "delete":
                    target.unlink()
                else:
                    _atomic_copy(source, target)
        except Exception:
            for target in created:
                if target.exists():
                    target.unlink()
            for saved in backup.rglob("*"):
                if saved.is_file():
                    _atomic_copy(saved, root / saved.relative_to(backup))
            raise
        for change, target, _source in targets:
            operation = {"create": ResourceOperation.CREATE, "modify": ResourceOperation.WRITE, "delete": ResourceOperation.DELETE}[change["operation"]]
            before = metadata["before"].get(change["path"], {})
            after_item = metadata["after"].get(change["path"], {})
            record_resource_activity(ResourceObservation(
                workspace_uri(root, change["path"]), operation, ObservationMode.EXACT,
                change_state=ChangeState.APPLIED, resource_bytes=int(after_item.get("size", 0)),
                before_digest=str(before.get("sha256", "")), after_digest=str(after_item.get("sha256", "")),
                metadata={"change_set_id": change_set_id},
            ))
        shutil.rmtree(directory, ignore_errors=True)
        emit_event(
            "command_changes_applied", "workspace_tools", "Command changes applied.",
            {"change_set_id": change_set_id, "file_count": len(changes)},
        )
        return f"Applied change set {change_set_id} with {len(changes)} file change(s)."

    @tool
    def discard_staged_changes(change_set_id: str) -> str:
        """Discard a staged command change set without changing the Workspace."""
        directory, metadata = _load_changeset(root, change_set_id)
        changes = metadata.get("changes", [])
        count = len(changes)
        for change in changes:
            operation = {"create": ResourceOperation.CREATE, "modify": ResourceOperation.WRITE, "delete": ResourceOperation.DELETE}[change["operation"]]
            record_resource_activity(ResourceObservation(
                workspace_uri(root, change["path"]), operation, ObservationMode.EXACT,
                change_state=ChangeState.DISCARDED, resource_bytes=int(change.get("size", 0)),
                metadata={"change_set_id": change_set_id},
            ))
        shutil.rmtree(directory, ignore_errors=True)
        emit_event(
            "command_changes_discarded", "workspace_tools", "Command changes discarded.",
            {"change_set_id": change_set_id, "file_count": count},
        )
        return f"Discarded change set {change_set_id}."

    return stage_command_changes, apply_staged_changes, discard_staged_changes
