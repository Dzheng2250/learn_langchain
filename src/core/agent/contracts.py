"""Stable service contracts shared by Core composition and RPC handlers."""

from collections.abc import Callable
from typing import Protocol


EventCallback = Callable[[dict], None]


class AgentTurnRunner(Protocol):
    async def run_turn(
        self,
        workspace_root: str,
        session_name: str,
        user_input: str,
        on_event: EventCallback | None = None,
        *,
        run_id: str | None = None,
    ) -> dict:
        """Execute one Agent turn without blocking the event loop."""


class ManagedAgentService(AgentTurnRunner, Protocol):
    def initialize(self) -> None:
        """Initialize durable dependencies."""

    def close(self) -> None:
        """Close service-owned resources."""
