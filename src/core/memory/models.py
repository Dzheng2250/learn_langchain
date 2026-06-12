"""Typed long-term memory values returned by retrieval strategies."""

from dataclasses import dataclass, field


@dataclass
class RetrievedMemory:
    """One long-term memory selected for the current user turn."""

    id: str
    kind: str
    content: str
    tags: list[str] = field(default_factory=list)
    importance: int = 3
    confidence: float = 1.0
