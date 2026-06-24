"""Stable service contracts shared by Core composition and RPC handlers."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from threading import Event
from typing import Protocol


EventCallback = Callable[[dict], None]


@dataclass
class ExecutionControl:
    """Cross-thread cooperative control signals for one request Grant."""

    pause_after_slice: Event = field(default_factory=Event)


class DiagnosticTurnStreamer(Protocol):
    """Stream infrastructure diagnostics when no model is configured."""

    def stream_unconfigured_turn(
        self,
        session,
        run_id: str,
        missing: tuple[str, ...],
    ) -> Iterator[dict]: ...


class ExecutionLifecycleController(Protocol):
    """Execution state-machine operations required by request routing."""

    def begin_turn(self, session, user_input: str, *, goal_mode: bool): ...

    def has_attached_execution(self, session) -> bool: ...

    def resume(self, session): ...

    def pause_runtime_creation_failed(self, execution, exc: Exception) -> None: ...

    def pause_resume_preparation_failed(self, execution, exc: Exception) -> None: ...


class RuntimeGraphProvider(Protocol):
    """Select a workspace graph for a new or resumed execution."""

    def graph_for_turn(self, workspace, *, goal_mode: bool): ...

    def graph_for_resume(self, workspace, pending, *, instruction: str = ""): ...


class LockedTurnStreamer(Protocol):
    """Run one already-locked foreground turn as a synchronous event stream."""

    def stream_locked_turn(
        self,
        session,
        graph,
        user_input: str,
        run_id: str,
        *,
        execution=None,
        resume: bool = False,
        control: ExecutionControl | None = None,
    ) -> Iterator[dict]: ...


class AgentTurnRunner(Protocol):
    """Minimal asynchronous Turn interface required by RPC handlers."""

    async def run_turn(
        self,
        workspace_root: str,
        session_name: str,
        user_input: str,
        on_event: EventCallback | None = None,
        *,
        run_id: str | None = None,
        control: ExecutionControl | None = None,
        goal_mode: bool = False,
    ) -> dict:
        """Execute one Agent turn without blocking the event loop."""

    async def resume_execution(
        self,
        workspace_root: str,
        session_name: str,
        instruction: str = "",
        on_event: EventCallback | None = None,
        *,
        run_id: str | None = None,
        control: ExecutionControl | None = None,
    ) -> dict:
        """Resume one recoverable execution without blocking the event loop."""


class SessionLifecycleController(Protocol):
    """Session control interface required by RPC handlers."""

    def session_status(self, workspace_root: str, session_name: str) -> dict:
        """Return compact pending-execution state."""

    def discard_pending(self, workspace_root: str, session_name: str) -> dict:
        """Discard the Session's pending execution."""

    def delete_session(self, workspace_root: str, session_name: str, *, hard_delete: bool = False) -> dict:
        """Archive or permanently delete one Session."""

    def reset_session(self, workspace_root: str, session_name: str) -> dict:
        """Rebuild recent_messages from archived message history.

        This recovers a Session whose ``recent_messages`` cache contains stale
        or provider-rejected content without destroying the Session or its
        archived message history. ``context_tokens`` is reset to 0 so the
        compression policy will re-evaluate the next context boundary.
        """


class ManagedAgentService(AgentTurnRunner, Protocol):
    """Turn runner whose durable dependencies have an explicit lifecycle."""

    def initialize(self) -> None:
        """Initialize durable dependencies."""

    def close(self) -> None:
        """Close service-owned resources."""
