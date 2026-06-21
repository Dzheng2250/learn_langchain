"""Build bounded Agent input and compress older Session conversation state."""

from langchain_core.messages import HumanMessage, SystemMessage

from src.config.settings import (
    RECENT_MESSAGE_LIMIT,
    SESSION_SUMMARY_MAX_CHARS,
    SUMMARY_SOURCE_CHAR_LIMIT,
    SUMMARY_TRIGGER_CHAR_LIMIT,
    SUMMARY_TRIGGER_MESSAGE_LIMIT,
    SUMMARY_TRIGGER_TOKEN_LIMIT,
)
from src.core.telemetry import emit_event, record_error
from src.core.context.messages import (
    SUMMARY_MESSAGE_PREFIX,
    strip_context_messages,
)
from src.core.context.models import AgentContextState
from src.core.context.summary_executor import ContextSummaryExecutor
from src.core.context.summary_policy import SummaryPolicy
from src.core.llm.contracts import ModelProvider


class AgentContextManager:
    """Build bounded LLM inputs and compress old conversation turns."""

    def __init__(
        self,
        model_provider: ModelProvider,
        recent_message_limit: int = RECENT_MESSAGE_LIMIT,
        summary_trigger_message_limit: int = SUMMARY_TRIGGER_MESSAGE_LIMIT,
        summary_trigger_char_limit: int = SUMMARY_TRIGGER_CHAR_LIMIT,
        summary_trigger_token_limit: int = SUMMARY_TRIGGER_TOKEN_LIMIT,
        summary_max_chars: int = SESSION_SUMMARY_MAX_CHARS,
        summary_source_char_limit: int = SUMMARY_SOURCE_CHAR_LIMIT,
    ) -> None:
        self.recent_message_limit = recent_message_limit
        self.summary_trigger_message_limit = summary_trigger_message_limit
        self.summary_trigger_char_limit = summary_trigger_char_limit
        self.summary_trigger_token_limit = summary_trigger_token_limit
        self.summary_max_chars = summary_max_chars
        self.summary_source_char_limit = summary_source_char_limit
        self.summary_executor = ContextSummaryExecutor(
            model_provider=model_provider,
            summary_max_chars=summary_max_chars,
            summary_source_char_limit=summary_source_char_limit,
        )
        self.summary_policy = SummaryPolicy(
            message_limit=summary_trigger_message_limit,
            char_limit=summary_trigger_char_limit,
            token_limit=summary_trigger_token_limit,
        )

    def build_input_messages(
        self,
        state: AgentContextState,
        user_input: str,
        extra_system_messages: list | None = None,
    ) -> list:
        """Build one bounded graph input from compact context state."""
        messages = []
        # Synthetic context messages are input-only. update_after_turn removes
        # them before recent conversation state is persisted.
        if state.summary:
            messages.append(SystemMessage(content=f"{SUMMARY_MESSAGE_PREFIX}\n{state.summary}"))

        if extra_system_messages:
            messages.extend(extra_system_messages)

        messages.extend(state.recent_messages[-self.recent_message_limit:])
        messages.append(HumanMessage(content=user_input))
        return messages

    def update_after_turn(
        self,
        state: AgentContextState,
        final_messages: list,
        force_summarize: bool = False,
        memory_context: str = "",
    ) -> AgentContextState:
        """Update compact context after one graph execution."""
        final_conversation_messages = strip_context_messages(final_messages)
        unsent_previous_messages = state.recent_messages[:-self.recent_message_limit]
        conversation_messages = [*unsent_previous_messages, *final_conversation_messages]

        should_summarize = (
            force_summarize
            or self.summary_policy.should_summarize_state(
                context_tokens=state.context_tokens,
                messages=conversation_messages,
            )
        )
        if not should_summarize:
            emit_event(
                "context_summary_skipped",
                "agent_context",
                "Context summary skipped.",
                {
                    "message_count": len(conversation_messages),
                    "recent_message_limit": self.recent_message_limit,
                    "summary_trigger_message_limit": self.summary_trigger_message_limit,
                    "summary_trigger_char_limit": self.summary_trigger_char_limit,
                },
            )
            return AgentContextState(
                summary=state.summary,
                recent_messages=conversation_messages,
                context_tokens=state.context_tokens,
            )

        old_messages = conversation_messages[:-self.recent_message_limit]
        recent_messages = conversation_messages[-self.recent_message_limit:]
        emit_event(
            "context_summarize_triggered",
            "agent_context",
            "Context summary triggered.",
            {
                "force_summarize": force_summarize,
                "old_message_count": len(old_messages),
                "recent_message_count": len(recent_messages),
            },
        )
        try:
            summary, summary_input_tokens, summary_output_tokens = self._summarize_messages(
                state.summary, old_messages, memory_context
            )
        except Exception as exc:
            record_error(
                "agent_context",
                "context_summary",
                exc,
                "Context summary failed, truncating to recent messages only.",
                {"old_message_count": len(old_messages)},
                event_type="context_summary_failed",
            )
            # Degrade gracefully: keep the previous summary and drop old
            # messages without compressing, to prevent unbounded growth.
            return AgentContextState(
                summary=state.summary,
                recent_messages=conversation_messages[-self.recent_message_limit:],
                context_tokens=state.context_tokens,
            )

        # context_tokens = previous - compressed_old_messages + new_summary
        new_context_tokens = max(
            0,
            state.context_tokens - summary_input_tokens + summary_output_tokens,
        )
        return AgentContextState(
            summary=summary,
            recent_messages=recent_messages,
            context_tokens=new_context_tokens,
        )

    def build_fast_state(self, state: AgentContextState, final_messages: list) -> AgentContextState:
        """Build bounded committed context without invoking a summary model."""
        final_conversation_messages = strip_context_messages(final_messages)
        unsent_previous_messages = state.recent_messages[:-self.recent_message_limit]
        conversation_messages = [*unsent_previous_messages, *final_conversation_messages]
        return AgentContextState(
            summary=state.summary,
            recent_messages=conversation_messages[-self.recent_message_limit:],
            context_tokens=state.context_tokens,
        )

    def should_summarize(self, messages: list) -> bool:
        """Expose the summary policy to durable maintenance handlers."""
        return self.summary_policy.should_summarize_messages(messages)

    def summarize_messages(self, previous_summary: str, messages: list, memory_context: str = "") -> str:
        """Create a derived summary outside the response critical path."""
        summary, _, _ = self._summarize_messages(previous_summary, messages, memory_context)
        return summary

    def extract_turn_messages(self, state: AgentContextState, final_messages: list) -> list:
        """Return only messages created by the current Turn.

        Synthetic summary and memory messages are removed first. This remains
        stable across checkpoint resumes even if injected memory changes.
        """
        conversation = strip_context_messages(final_messages)
        loaded_recent_count = min(len(state.recent_messages), self.recent_message_limit)
        return conversation[loaded_recent_count:]

    def _summarize_messages(self, previous_summary: str, messages: list, memory_context: str = "") -> tuple[str, int, int]:
        """Compress older messages into a structured session summary.

        Returns:
            ``(summary, input_tokens, output_tokens)`` — the compressed summary
            text and the token counts from the summary LLM call.
        """
        return self.summary_executor.summarize(
            previous_summary,
            messages,
            memory_context,
        )

    def _create_summary_llm(self):
        """Compatibility wrapper for tests and older internal callers."""
        return self.summary_executor._create_summary_llm()
