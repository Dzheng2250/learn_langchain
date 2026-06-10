from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from src.config.settings import DEFAULT_SESSION_ID


@dataclass
class AgentEvent:
    """Structured observation emitted by the agent harness."""

    event_type: str
    source: str
    message: str = ""
    payload: dict = field(default_factory=dict)
    level: str = "info"
    session_id: str = DEFAULT_SESSION_ID
    turn_index: int | None = None
    run_id: str = ""
    duration_ms: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AgentEventContext:
    """Context shared by events emitted during one agent turn."""

    session_id: str = DEFAULT_SESSION_ID
    turn_index: int | None = None
    run_id: str = ""


@dataclass(frozen=True)
class HookHelperSpec:
    """Public helper metadata for discoverability and conventions."""

    name: str
    event_types: tuple[str, ...]
    description: str


class EventSink(Protocol):
    """Destination for structured agent events."""

    def emit(self, event: AgentEvent) -> None:
        """Write one event."""
