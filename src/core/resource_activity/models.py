"""Stable resource activity vocabulary exposed by Core."""
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = 1

class ResourceOperation(StrEnum):
    READ="read"; SUMMARIZE="summarize"; CREATE="create"; WRITE="write"; MOVE="move"; DELETE="delete"
class ObservationMode(StrEnum):
    EXACT="exact"; RANGE="range"; SUMMARY="summary"; SCOPE_ONLY="scope_only"; UNKNOWN="unknown"
class ChangeState(StrEnum):
    OBSERVED="observed"; PROPOSED="proposed"; APPLIED="applied"; DISCARDED="discarded"
class EvidenceStatus(StrEnum):
    CURRENT="current"; PARTIAL="partial"; STALE="stale"; MISSING="missing"; INCOMPLETE="incomplete"; NOT_APPLICABLE="not_applicable"

@dataclass(frozen=True)
class ResourceObservation:
    resource_uri: str
    operation: ResourceOperation
    observation_mode: ObservationMode
    change_state: ChangeState = ChangeState.OBSERVED
    requested_range: dict[str, Any] | None = None
    observed_range: dict[str, Any] | None = None
    returned_bytes: int = 0
    resource_bytes: int = 0
    before_digest: str = ""
    after_digest: str = ""
    before_lines: int | None = None
    after_lines: int | None = None
    related_activity_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    event_key: str = ""

@dataclass(frozen=True)
class ResourceActivitySummary:
    scope: dict[str, Any]
    reads: dict[str, Any]
    changes: dict[str, Any]
    evidence: dict[str, Any]
    truncated: bool = False
    schema_version: int = SCHEMA_VERSION
    def to_dict(self) -> dict[str, Any]: return asdict(self)
