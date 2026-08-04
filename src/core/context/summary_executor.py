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
        if summary_source_char_limit <= 0:
            raise ValueError("summary_source_char_limit must be greater than zero")
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
        chunks = self._source_chunks(messages)
        summary = previous_summary
        input_tokens = 0
        output_tokens = 0
        llm = self._create_summary_llm()
        for chunk_index, source in enumerate(chunks, start=1):
            with event_span(
                "context_summary_llm",
                "agent_context",
                payload={
                    "source_chars": len(source),
                    "message_count": len(messages),
                    "chunk_index": chunk_index,
                    "chunk_count": len(chunks),
                },
            ):
                response = llm.invoke(
                    build_context_summary_messages(
                        source=source,
                        previous_summary=summary,
                        memory_context=memory_context,
                        summary_max_chars=self.summary_max_chars,
                    )
                )
            summary = message_content_text(response).strip()
            if not summary:
                raise RuntimeError(
                    f"Context summary model returned empty output for chunk {chunk_index}."
                )
            if len(summary) > self.summary_max_chars:
                suffix = "\n... summary truncated ..."
                summary = summary[: max(0, self.summary_max_chars - len(suffix))] + suffix
            chunk_input, chunk_output = self._usage_tokens(response)
            input_tokens += chunk_input
            output_tokens += chunk_output
        debug_print("CONTEXT SUMMARY UPDATED", summary)
        emit_event(
            "context_summarized",
            "agent_context",
            "Context summary updated.",
            {
                "summary_chars": len(summary),
                "compressed_messages": len(messages),
                "source_chunks": len(chunks),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        )
        return summary, input_tokens, output_tokens

    def _source_chunks(self, messages: list) -> list[str]:
        """Format every source message exactly once without suffix truncation."""
        chunks: list[str] = []
        current = ""
        for message in messages:
            source = format_messages_for_summary([message])
            parts = [
                source[index:index + self.summary_source_char_limit]
                for index in range(0, len(source), self.summary_source_char_limit)
            ] or [""]
            for part in parts:
                separator = "\n\n" if current and part else ""
                if current and len(current) + len(separator) + len(part) > self.summary_source_char_limit:
                    chunks.append(current)
                    current = part
                else:
                    current = f"{current}{separator}{part}"
        if current or not chunks:
            chunks.append(current)
        return chunks

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
