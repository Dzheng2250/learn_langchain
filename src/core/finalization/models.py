"""Data transferred across the Turn finalization boundary."""

from dataclasses import dataclass, field

from src.core.context.models import AgentContextState
from src.core.maintenance.models import MaintenanceJobSpec
from src.core.workspace.models import SessionContext


@dataclass(frozen=True)
class CompletedTurn:
    """All facts required for the minimal durable Turn commit."""

    session: SessionContext
    turn_index: int
    messages: list
    state: AgentContextState
    execution_id: str | None = None
    checkpoint_thread_id: str | None = None
    slice_id: str | None = None
    graph_steps_used: int = 0
    usage: dict = field(default_factory=dict)
    jobs: tuple[MaintenanceJobSpec, ...] = ()


@dataclass(frozen=True)
class FinalizationResult:
    """User-visible durability state after the minimal commit succeeds."""

    message_ids: tuple[str, ...]
    maintenance_status: str
    memory_status: str
    memory_request_explicit: bool
