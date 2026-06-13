"""Models and contracts for durable Core telemetry events."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class TelemetryContext:
    """Identity attached to telemetry produced during one Agent turn."""

    workspace_id: UUID | None = None
    session_id: UUID | None = None
    turn_index: int | None = None
    run_id: str = ""


@dataclass(frozen=True)
class TelemetryEvent:
    """Sanitized observation envelope delivered to every configured sink."""

    event_type: str
    source: str
    message: str = ""
    payload: dict = field(default_factory=dict)
    level: str = "info"
    workspace_id: UUID | None = None
    session_id: UUID | None = None
    turn_index: int | None = None
    run_id: str = ""
    duration_ms: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EventSink(Protocol):
    """Lifecycle-aware destination consumed by :class:`EventBus`."""

    def emit(self, event: TelemetryEvent) -> None:
        """Accept one event."""

    def flush(self) -> None:
        """Finish pending writes."""

    def close(self) -> None:
        """Release resources."""


class BatchEventSink(Protocol):
    """Destination that can persist multiple events in one operation."""

    def emit_batch(self, events: list[TelemetryEvent]) -> None:
        """Accept one event batch."""

    def flush(self) -> None:
        """Finish pending writes."""

    def close(self) -> None:
        """Release resources."""
