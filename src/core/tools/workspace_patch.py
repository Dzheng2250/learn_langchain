"""Parse and apply snapshot-based Workspace patches."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile
from threading import Lock
from typing import Callable

from lark import Lark, UnexpectedInput

from src.core.tools.workspace import resolve_workspace_mutation_path
from src.core.tools.errors import ToolSideEffectUncertain
from src.core.workspace.resolver import canonicalize_workspace


_PATCH_GRAMMAR = r"""
start: BEGIN NEWLINE update+ END NEWLINE?
update: UPDATE NEWLINE hunk+
hunk: HUNK NEWLINE patch_line+
patch_line: CONTEXT NEWLINE | ADD NEWLINE | REMOVE NEWLINE

BEGIN.10: "*** Begin Patch"
END.10: "*** End Patch"
UPDATE.10: /\*\*\* Update File: [^\r\n]+/
HUNK.10: /@@[^\r\n]*/
CONTEXT: / [^\r\n]*/
ADD: /\+[^\r\n]*/
REMOVE: /-[^\r\n]*/
NEWLINE: /\r?\n/
"""

_PARSER = Lark(_PATCH_GRAMMAR, parser="lalr", lexer="contextual")
_PATH_LOCK_GUARD = Lock()
_PATH_LOCKS: dict[str, Lock] = {}


class WorkspacePatchError(ValueError):
    """A safe, model-visible patch validation failure."""


@dataclass(frozen=True)
class PatchLine:
    kind: str
    text: str


@dataclass(frozen=True)
class PatchHunk:
    anchor: str
    lines: tuple[PatchLine, ...]


@dataclass(frozen=True)
class FilePatch:
    path: str
    hunks: tuple[PatchHunk, ...]


@dataclass(frozen=True)
class WorkspacePatch:
    files: tuple[FilePatch, ...]

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(item.path for item in self.files)

    @property
    def hunk_count(self) -> int:
        return sum(len(item.hunks) for item in self.files)


@dataclass(frozen=True)
class PatchApplication:
    files: int
    hunks: int
    additions: int
    deletions: int
    paths: tuple[str, ...]
    snapshots: tuple[tuple[Path, dict, dict, int, int, int], ...]


@dataclass(frozen=True)
class _PreparedFile:
    path: str
    target: Path
    original: bytes
    updated: bytes
    before: dict
    after: dict
    additions: int
    deletions: int
    hunks: int


def parse_workspace_patch(
    source: str,
    *,
    max_files: int = 100,
    max_hunks: int = 100,
) -> WorkspacePatch:
    """Parse the restricted Codex-style update-only patch protocol."""
    if not isinstance(source, str) or not source:
        raise WorkspacePatchError("patch must be a non-empty string")
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    try:
        _PARSER.parse(normalized)
    except UnexpectedInput as exc:
        raise WorkspacePatchError(
            f"invalid patch syntax near line {exc.line}, column {exc.column}"
        ) from None

    lines = normalized.splitlines()
    files: list[FilePatch] = []
    seen_paths: set[str] = set()
    current_path = ""
    current_hunks: list[PatchHunk] = []
    current_anchor: str | None = None
    current_lines: list[PatchLine] = []

    def finish_hunk() -> None:
        nonlocal current_anchor, current_lines
        if current_anchor is None:
            return
        old_lines = [line for line in current_lines if line.kind != "add"]
        if not old_lines:
            raise WorkspacePatchError(
                f"{current_path}: hunk {len(current_hunks) + 1} needs context or removed lines"
            )
        if not any(line.kind in {"add", "remove"} for line in current_lines):
            raise WorkspacePatchError(
                f"{current_path}: hunk {len(current_hunks) + 1} contains no changes"
            )
        current_hunks.append(PatchHunk(current_anchor, tuple(current_lines)))
        current_anchor = None
        current_lines = []

    def finish_file() -> None:
        nonlocal current_path, current_hunks
        if not current_path:
            return
        finish_hunk()
        files.append(FilePatch(current_path, tuple(current_hunks)))
        current_path = ""
        current_hunks = []

    for line in lines[1:-1]:
        if line.startswith("*** Update File: "):
            finish_file()
            current_path = line.removeprefix("*** Update File: ").strip()
            if not current_path:
                raise WorkspacePatchError("update path must not be empty")
            normalized_path = current_path.replace("\\", "/")
            if normalized_path in seen_paths:
                raise WorkspacePatchError(
                    f"duplicate Update File section for {normalized_path}"
                )
            seen_paths.add(normalized_path)
            current_path = normalized_path
        elif line.startswith("@@"):
            finish_hunk()
            current_anchor = line[2:].strip()
        elif current_anchor is not None and line[:1] in {" ", "+", "-"}:
            current_lines.append(
                PatchLine({" ": "context", "+": "add", "-": "remove"}[line[0]], line[1:])
            )

    finish_file()
    if len(files) > max_files:
        raise WorkspacePatchError(
            f"patch affects {len(files)} files; limit is {max_files}"
        )
    hunk_count = sum(len(item.hunks) for item in files)
    if hunk_count > max_hunks:
        raise WorkspacePatchError(
            f"patch contains {hunk_count} hunks; limit is {max_hunks}"
        )
    return WorkspacePatch(tuple(files))


def patch_paths(source: str, *, max_files: int = 100, max_hunks: int = 100) -> tuple[str, ...]:
    """Return validated target paths for policy, approval, and ledger use."""
    return parse_workspace_patch(
        source, max_files=max_files, max_hunks=max_hunks
    ).paths


def apply_file_patch(original: str, patch: FilePatch) -> tuple[str, int, int]:
    """Apply every hunk against one immutable logical-line snapshot."""
    newline = "\r\n" if "\r\n" in original else "\n"
    final_newline = original.endswith(("\n", "\r"))
    original_lines = original.splitlines()
    replacements: list[tuple[int, int, list[str]]] = []
    cursor = 0
    additions = 0
    deletions = 0

    for hunk_index, hunk in enumerate(patch.hunks, start=1):
        search_start = cursor
        if hunk.anchor:
            anchors = [
                index
                for index in range(cursor, len(original_lines))
                if hunk.anchor in original_lines[index]
            ]
            if not anchors:
                raise WorkspacePatchError(
                    f"{patch.path}: hunk {hunk_index} anchor was not found"
                )
            # The anchor is a forward-search hint, not the final identity.
            # Exact hunk context below remains responsible for uniqueness.
            search_start = anchors[0]

        old = [line.text for line in hunk.lines if line.kind != "add"]
        new = [line.text for line in hunk.lines if line.kind != "remove"]
        matches = [
            index
            for index in range(search_start, len(original_lines) - len(old) + 1)
            if original_lines[index : index + len(old)] == old
        ]
        if not matches:
            raise WorkspacePatchError(
                f"{patch.path}: hunk {hunk_index} context does not match the current file"
            )
        if len(matches) > 1:
            raise WorkspacePatchError(
                f"{patch.path}: hunk {hunk_index} context is ambiguous"
            )
        start = matches[0]
        end = start + len(old)
        if start < cursor:
            raise WorkspacePatchError(
                f"{patch.path}: hunk {hunk_index} overlaps or is out of order"
            )
        replacements.append((start, end, new))
        cursor = end
        additions += sum(1 for line in hunk.lines if line.kind == "add")
        deletions += sum(1 for line in hunk.lines if line.kind == "remove")

    updated_lines = list(original_lines)
    for start, end, replacement in reversed(replacements):
        updated_lines[start:end] = replacement
    updated = newline.join(updated_lines)
    if final_newline:
        updated += newline
    return updated, additions, deletions


class WorkspacePatchEngine:
    """Validate a complete patch before atomically replacing any target."""

    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int,
        max_files: int = 100,
        max_hunks: int = 100,
        replace: Callable[[str | os.PathLike, str | os.PathLike], None] = os.replace,
    ) -> None:
        self.root = canonicalize_workspace(root)
        self.max_bytes = max_bytes
        self.max_files = max_files
        self.max_hunks = max_hunks
        self._replace = replace

    def apply(self, source: str) -> PatchApplication:
        patch = parse_workspace_patch(
            source, max_files=self.max_files, max_hunks=self.max_hunks
        )
        targets = [
            resolve_workspace_mutation_path(self.root, item.path)
            for item in patch.files
        ]
        target_keys = [os.path.normcase(str(target)) for target in targets]
        if len(target_keys) != len(set(target_keys)):
            raise WorkspacePatchError(
                "patch contains multiple paths that resolve to the same target"
            )
        with _locked_paths(targets):
            prepared = [
                self._prepare(item, target)
                for item, target in zip(patch.files, targets, strict=True)
            ]
            self._verify_snapshots(prepared)
            temporaries = self._write_temporaries(prepared)
            committed: list[_PreparedFile] = []
            try:
                for item in sorted(prepared, key=lambda value: str(value.target)):
                    self._verify_one(item)
                    self._replace(temporaries[item.target], item.target)
                    temporaries.pop(item.target, None)
                    committed.append(item)
            except Exception as exc:
                rollback_errors = self._rollback(committed)
                if rollback_errors:
                    raise ToolSideEffectUncertain(
                        "patch commit failed and rollback was incomplete: "
                        + "; ".join(rollback_errors)
                    ) from exc
                raise
            finally:
                for temporary in temporaries.values():
                    try:
                        Path(temporary).unlink()
                    except OSError:
                        pass

        return PatchApplication(
            len(prepared),
            patch.hunk_count,
            sum(item.additions for item in prepared),
            sum(item.deletions for item in prepared),
            tuple(item.path for item in prepared),
            tuple(
                (
                    item.target,
                    item.before,
                    item.after,
                    item.additions,
                    item.deletions,
                    item.hunks,
                )
                for item in prepared
            ),
        )

    def _prepare(self, item: FilePatch, target: Path) -> _PreparedFile:
        if not target.is_file():
            raise WorkspacePatchError(f"{item.path}: target is not an existing regular file")
        try:
            original_bytes = target.read_bytes()
            original = original_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise WorkspacePatchError(f"{item.path}: target is not valid UTF-8 text") from None
        updated, additions, deletions = apply_file_patch(original, item)
        updated_bytes = updated.encode("utf-8")
        if len(updated_bytes) > self.max_bytes:
            raise WorkspacePatchError(
                f"{item.path}: result exceeds the {self.max_bytes}-byte write limit"
            )
        return _PreparedFile(
            item.path,
            target,
            original_bytes,
            updated_bytes,
            _snapshot_bytes(original_bytes),
            _snapshot_bytes(updated_bytes),
            additions,
            deletions,
            len(item.hunks),
        )

    @staticmethod
    def _verify_snapshots(items: list[_PreparedFile]) -> None:
        for item in items:
            WorkspacePatchEngine._verify_one(item)

    @staticmethod
    def _verify_one(item: _PreparedFile) -> None:
        try:
            current = item.target.read_bytes()
        except OSError as exc:
            raise WorkspacePatchError(f"{item.path}: target changed before commit") from exc
        if hashlib.sha256(current).hexdigest() != item.before["digest"]:
            raise WorkspacePatchError(f"{item.path}: target changed before commit")

    @staticmethod
    def _write_temporaries(items: list[_PreparedFile]) -> dict[Path, str]:
        result: dict[Path, str] = {}
        try:
            for item in items:
                descriptor, temporary = tempfile.mkstemp(
                    prefix=f".{item.target.name}.", dir=item.target.parent
                )
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(item.updated)
                    handle.flush()
                    os.fsync(handle.fileno())
                result[item.target] = temporary
            return result
        except Exception:
            for temporary in result.values():
                try:
                    Path(temporary).unlink()
                except OSError:
                    pass
            raise

    def _rollback(self, committed: list[_PreparedFile]) -> list[str]:
        errors: list[str] = []
        for item in reversed(committed):
            temporary = ""
            try:
                descriptor, temporary = tempfile.mkstemp(
                    prefix=f".{item.target.name}.rollback.", dir=item.target.parent
                )
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(item.original)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._replace(temporary, item.target)
            except Exception as exc:
                errors.append(f"{item.path}: {type(exc).__name__}")
                if temporary:
                    try:
                        Path(temporary).unlink()
                    except OSError:
                        pass
        return errors


@contextmanager
def _locked_paths(paths: list[Path]):
    keys = sorted({os.path.normcase(str(path)) for path in paths})
    with _PATH_LOCK_GUARD:
        locks = [_PATH_LOCKS.setdefault(key, Lock()) for key in keys]
    for lock in locks:
        lock.acquire()
    try:
        yield
    finally:
        for lock in reversed(locks):
            lock.release()


def _snapshot_bytes(data: bytes) -> dict:
    return {
        "bytes": len(data),
        "lines": len(data.decode("utf-8").splitlines()),
        "digest": hashlib.sha256(data).hexdigest(),
    }


__all__ = [
    "FilePatch",
    "PatchApplication",
    "PatchHunk",
    "PatchLine",
    "WorkspacePatch",
    "WorkspacePatchEngine",
    "WorkspacePatchError",
    "apply_file_patch",
    "parse_workspace_patch",
    "patch_paths",
]
