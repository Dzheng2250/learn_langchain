"""Background-maintenance persistence ports.

These capabilities are intentionally split by maintenance use case so a
handler does not receive the complete local-state facade.
"""

from __future__ import annotations

from typing import Protocol

from src.core.workspace.models import SessionContext
from src.core.context.models import ContextWindowSource


class SummaryMaintenanceStore(Protocol):
    """Read summary input and apply one compare-and-swap summary update."""

    def load_summary_source(
        self,
        session: SessionContext,
        target_turn: int,
    ) -> ContextWindowSource: ...

    def update_summary_cas(
        self,
        session: SessionContext,
        *,
        expected_window_id: str,
        summary_through_turn: int,
        summary: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model: str = "",
    ) -> bool: ...


class MemoryWriteStore(Protocol):
    """Extract and persist memories from one committed conversation Turn."""

    def extract_and_save(
        self,
        session: SessionContext,
        turn_index: int,
        messages: list,
        source_message_ids: list[str],
    ) -> list[str]: ...
