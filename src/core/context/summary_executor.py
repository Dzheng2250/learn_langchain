"""LLM-backed execution of context summary compression."""

from src.config.settings import SESSION_SUMMARY_MAX_CHARS, SUMMARY_SOURCE_CHAR_LIMIT
from src.core.common.content import message_content_text
from src.core.common.debug import debug_print
from src.core.context.messages import format_messages_for_summary
from src.core.llm.contracts import LlmPurpose, ModelProvider
from src.core.prompts import build_context_summary_messages
from src.core.telemetry import emit_event, event_span


class ContextSummaryExecutor:
    """Compress old conversation messages with the configured summary model."""

    def __init__(
        self,
        *,
        model_provider: ModelProvider,
        summary_max_chars: int = SESSION_SUMMARY_MAX_CHARS,
        summary_source_char_limit: int = SUMMARY_SOURCE_CHAR_LIMIT,
    ) -> None:
        self.model_provider = model_provider
        self.summary_max_chars = summary_max_chars
        self.summary_source_char_limit = summary_source_char_limit

    def summarize(
        self,
        previous_summary: str,
        messages: list,
        memory_context: str = "",
    ) -> tuple[str, int, int]:
        """Return `(summary, input_tokens, output_tokens)` for old messages."""
        source = format_messages_for_summary(messages)
        if len(source) > self.summary_source_char_limit:
            source = source[-self.summary_source_char_limit:]

        llm = self._create_summary_llm()
        with event_span(
            "context_summary_llm",
            "agent_context",
            payload={"source_chars": len(source), "message_count": len(messages)},
        ):
            response = llm.invoke(
                build_context_summary_messages(
                    source=source,
                    previous_summary=previous_summary,
                    memory_context=memory_context,
                    summary_max_chars=self.summary_max_chars,
                )
            )

        summary = message_content_text(response).strip()
        if len(summary) > self.summary_max_chars:
            summary = summary[:self.summary_max_chars] + "\n... summary truncated ..."

        input_tokens, output_tokens = self._usage_tokens(response)
        debug_print("CONTEXT SUMMARY UPDATED", summary)
        emit_event(
            "context_summarized",
            "agent_context",
            "Context summary updated.",
            {
                "summary_chars": len(summary),
                "compressed_messages": len(messages),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        )
        return summary, input_tokens, output_tokens

    def _create_summary_llm(self):
        """Create a non-streaming model for context summarization."""
        return self.model_provider.create_chat_model(
            LlmPurpose.CONTEXT_SUMMARY,
            temperature=0,
            streaming=False,
        )

    @staticmethod
    def _usage_tokens(response) -> tuple[int, int]:
        try:
            metadata = getattr(response, "usage_metadata", None) or {}
            return metadata.get("input_tokens", 0), metadata.get("output_tokens", 0)
        except Exception:
            return 0, 0
