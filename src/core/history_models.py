"""Backend-neutral records used by Session conversation history queries."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HistoryMessageRecord:
    """One durable message row before frontend-safe normalization."""

    message_id: str
    turn_index: int
    message_ordinal: int
    role: str
    message_type: str
    content: str
    raw: dict[str, Any] | None


@dataclass(frozen=True)
class ConversationHistoryPage:
    """One complete-Turn page in chronological display order."""

    messages: tuple[HistoryMessageRecord, ...]
    next_before_turn: int | None
    has_more: bool
