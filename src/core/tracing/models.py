"""Stable models for the cross-layer daemon trace timeline."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


class TraceDirection(StrEnum):
    CLIENT_TO_CORE = "CLIENT_TO_CORE"
    CORE_TO_CLIENT = "CORE_TO_CLIENT"
    CORE_TO_PROVIDER = "CORE_TO_PROVIDER"
    PROVIDER_TO_CORE = "PROVIDER_TO_CORE"
    INTERNAL = "INTERNAL"


class TraceLayer(StrEnum):
    IPC = "ipc"
    AGENT = "agent"
    LLM = "llm"
    TOOL = "tool"
    TELEMETRY = "telemetry"
    LIFECYCLE = "lifecycle"


@dataclass(frozen=True)
class TraceRecord:
    """One sanitized observation in the daemon-wide ordered timeline."""

    schema_version: int
    daemon_id: str
    sequence: int
    timestamp: datetime
    monotonic_ns: int
    direction: TraceDirection
    layer: TraceLayer
    kind: str
    trace_id: str
    request_id: str | None = None
    run_id: str | None = None
    execution_id: str | None = None
    slice_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    client_id: str | None = None
    duration_ms: int | None = None
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Return a JSON-compatible representation."""
        value = asdict(self)
        value["timestamp"] = self.timestamp.astimezone(timezone.utc).isoformat()
        # Keep the wire format explicit if the internal model implementation
        # later changes, for example from dataclass to Pydantic.
        value["direction"] = self.direction.value
        value["layer"] = self.layer.value
        return value
