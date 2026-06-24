"""Summary trigger policy for compact Session context."""

from src.core.common.content import message_content_text


class SummaryPolicy:
    """Decide when recent conversation state should be summarized."""

    def __init__(
        self,
        *,
        message_limit: int,
        char_limit: int,
        token_limit: int,
    ) -> None:
        self.message_limit = message_limit
        self.char_limit = char_limit
        self.token_limit = token_limit

    def should_summarize_state(self, *, context_tokens: int, messages: list) -> bool:
        """Return whether stored context is large enough to summarize."""
        return (
            context_tokens > self.token_limit
            or self.should_summarize_messages(messages)
        )

    def should_summarize_messages(self, messages: list) -> bool:
        """Return whether message volume is large enough to compress."""
        if len(messages) > self.message_limit:
            return True
        return self._message_chars(messages) > self.char_limit

    @staticmethod
    def _message_chars(messages: list) -> int:
        total_chars = 0
        for message in messages:
            total_chars += len(message_content_text(message))
        return total_chars
