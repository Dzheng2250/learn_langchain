"""Immutable workspace and session identities used by Core services."""

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True)
class WorkspaceContext:
    workspace_id: UUID
    root: Path


@dataclass(frozen=True)
class SessionContext:
    session_id: UUID
    session_name: str
    workspace: WorkspaceContext
