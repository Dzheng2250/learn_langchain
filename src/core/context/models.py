"""Compact Session context persisted separately from full message history."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TurnChunk:
    """All LangChain messages created by one committed conversation Turn."""

    turn_index: int
    messages: list = field(default_factory=list)


@dataclass
class AgentContextState:
    """Compact conversation state kept outside LangGraph message history."""

    summary: str = ""
    recent_turns: list[TurnChunk] = field(default_factory=list)
    context_tokens: int = 0
    """Estimated token count of the current context (summary + recent + system)."""

    def __init__(
        self,
        summary: str = "",
        recent_messages: list | None = None,
        context_tokens: int = 0,
        *,
        recent_turns: list[TurnChunk] | None = None,
    ) -> None:
        self.summary = summary
        self.context_tokens = context_tokens
        if recent_turns is not None:
            self.recent_turns = list(recent_turns)
        elif recent_messages:
            # Legacy callers did not know the original Turn boundary. Keep the
            # messages together as one synthetic legacy Turn until the next save
            # rewrites the cache in the turn-aware format.
            self.recent_turns = [TurnChunk(0, list(recent_messages))]
        else:
            self.recent_turns = []

    @property
    def recent_messages(self) -> list:
        """Backward-compatible flattened view of recent Turns."""
        return [message for turn in self.recent_turns for message in turn.messages]
