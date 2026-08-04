"""Compact Session context persisted separately from full message history."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TurnChunk:
    """All LangChain messages created by one committed conversation Turn."""

    turn_index: int
    messages: list = field(default_factory=list)


@dataclass(frozen=True)
class ContextWindowSource:
    """One immutable summary head plus its unsummarized committed Turn tail."""

    window_id: str
    summary: str
    summary_through_turn: int
    turns: tuple[TurnChunk, ...]
    message_ids: tuple[str, ...] = ()

    @property
    def indexed_messages(self) -> list[tuple[int, object]]:
        """Return the legacy flattened view during the port migration period."""
        return [
            (turn.turn_index, message)
            for turn in self.turns
            for message in turn.messages
        ]

    def __iter__(self):
        """Support the former ``summary, watermark, messages`` unpacking API."""
        yield self.summary
        yield self.summary_through_turn
        yield self.indexed_messages


@dataclass
class AgentContextState:
    """Compact conversation state kept outside LangGraph message history."""

    summary: str = ""
    recent_turns: list[TurnChunk] = field(default_factory=list)
    context_tokens: int = 0
    """Estimated token count of the current context (summary + recent + system)."""
    context_window_id: str = ""
    summary_through_turn: int = 0

    def __init__(
        self,
        summary: str = "",
        recent_messages: list | None = None,
        context_tokens: int = 0,
        *,
        recent_turns: list[TurnChunk] | None = None,
        context_window_id: str = "",
        summary_through_turn: int = 0,
    ) -> None:
        self.summary = summary
        self.context_tokens = context_tokens
        self.context_window_id = context_window_id
        self.summary_through_turn = int(summary_through_turn)
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
