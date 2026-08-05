"""Execution persistence capabilities consumed by Agent application services."""

from __future__ import annotations

from typing import Any, Protocol

from src.core.state.types import CheckpointState, ExecutionStatus
from src.core.workspace.models import SessionContext


class ExecutionPauseStore(Protocol):
    """Persist a recoverable or terminal pause without exposing SQL."""

    def pause(
        self,
        execution_id: str,
        status: ExecutionStatus | str,
        stop_reason: str,
        summary: str = "",
        *,
        usage: dict | None = None,
        checkpoint_state: CheckpointState | str = CheckpointState.AVAILABLE,
        resume_policy: str | None = None,
        pause_fingerprint: str = "",
        pause_metadata: dict | None = None,
    ) -> None: ...


class ExecutionLifecycleStore(ExecutionPauseStore, Protocol):
    """Create, find, resume, and pause durable Executions."""

    def get_pending(self, session: SessionContext) -> Any | None: ...

    def get_attached(self, session: SessionContext) -> Any | None: ...

    def begin(
        self,
        session: SessionContext,
        user_input: str,
        *,
        goal_mode: bool = False,
    ) -> Any: ...

    def resume(
        self,
        session: SessionContext,
        *,
        resume_value: dict | None = None,
        retry_conditions: bool = False,
    ) -> Any: ...


class ExecutionSliceStore(Protocol):
    """Persist one bounded Slice lifecycle without exposing database details."""

    def start_slice(
        self,
        execution_id: str,
        grant_index: int,
        slice_index: int,
    ) -> str: ...

    def finish_slice(
        self,
        slice_id: str,
        execution_id: str,
        *,
        status: ExecutionStatus | str,
        stop_reason: str,
        graph_steps_used: int = 0,
        usage: dict | None = None,
    ) -> None: ...


class ExecutionFailureStore(ExecutionPauseStore, ExecutionSliceStore, Protocol):
    """Persist only the state transitions required by loop failures."""
