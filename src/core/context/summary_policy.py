"""Summary trigger policy for compact Session context."""

class SummaryPolicy:
    """Decide when recent conversation state should be summarized."""

    def __init__(
        self,
        *,
        turn_limit: int | None = None,
        message_limit: int | None = None,
        token_limit: int,
        token_limit_enabled: bool = True,
    ) -> None:
        # message_limit is accepted for compatibility with older tests and
        # internal callers, but it now means the number of complete Turns.
        self.turn_limit = int(turn_limit if turn_limit is not None else message_limit)
        self.token_limit_enabled = bool(token_limit_enabled)
        self.token_limit = token_limit
        self.message_limit = self.turn_limit

    def should_summarize_state(
        self,
        *,
        context_tokens: int,
        turns: list | None = None,
        messages: list,
    ) -> bool:
        """Return whether stored context is large enough to summarize."""
        turn_count = len(turns) if turns is not None else None
        return (
            (self.token_limit_enabled and context_tokens > self.token_limit)
            or self.should_summarize_messages(messages, turn_count=turn_count)
        )

    def should_summarize_messages(
        self,
        messages: list,
        *,
        turn_count: int | None = None,
    ) -> bool:
        """Return whether Turn count or message volume is large enough."""
        if turn_count is not None and turn_count > self.turn_limit:
            return True
        if turn_count is None and len(messages) > self.turn_limit:
            return True
        return False
