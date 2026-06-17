"""Stable service contracts shared by Core composition and RPC handlers."""

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event
from typing import Protocol


EventCallback = Callable[[dict], None]


@dataclass
class ExecutionControl:
    """Cross-thread cooperative control signals for one request Grant."""

    pause_after_slice: Event = field(default_factory=Event)


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

    def session_status(self, workspace_root: str, session_name: str) -> dict:
        """Return compact pending-execution state."""

    def discard_pending(self, workspace_root: str, session_name: str) -> dict:
        """Discard the Session's pending execution."""

    def delete_session(self, workspace_root: str, session_name: str, *, hard_delete: bool = False) -> dict:
        """Archive or permanently delete one Session."""


class ManagedAgentService(AgentTurnRunner, Protocol):
    """Turn runner whose durable dependencies have an explicit lifecycle."""

    def initialize(self) -> None:
        """Initialize durable dependencies."""

    def close(self) -> None:
        """Close service-owned resources."""
