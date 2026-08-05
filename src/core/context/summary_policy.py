"""Token-only summary trigger policy for compact Session context."""

from src.core.context.budget import ModelTokenCounter

class SummaryPolicy:
    """Decide when recent conversation state should be summarized."""

    def __init__(
        self,
        *,
        turn_limit: int | None = None,
        message_limit: int | None = None,
        token_limit: int,
        token_limit_enabled: bool = True,
        counter=None,
    ) -> None:
        # Turn/message limits remain accepted for one compatibility period,
        # but they no longer trigger compaction.
        self.turn_limit = int(turn_limit if turn_limit is not None else message_limit)
        self.token_limit_enabled = bool(token_limit_enabled)
        self.token_limit = token_limit
        self.message_limit = self.turn_limit
        self.counter = counter or ModelTokenCounter()

    def should_summarize_state(
        self,
        *,
        context_tokens: int,
        turns: list | None = None,
        messages: list,
    ) -> bool:
        """Return whether stored context is large enough to summarize."""
        if not self.token_limit_enabled:
            return False
        if context_tokens > self.token_limit:
            return True
        return self.should_summarize_messages(messages)

    def should_summarize_messages(
        self,
        messages: list,
        *,
        turn_count: int | None = None,
    ) -> bool:
        """Estimate message tokens for legacy callers without usage metadata."""
        del turn_count
        return bool(
            self.token_limit_enabled
            and self.counter.count_messages(messages).tokens > self.token_limit
        )
