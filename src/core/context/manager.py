"""Build bounded Agent input and compress older Session conversation state."""

from langchain_core.messages import HumanMessage, SystemMessage

from src.config.settings import (
    RECENT_TURN_LIMIT,
    SESSION_SUMMARY_MAX_CHARS,
    SUMMARY_SOURCE_CHAR_LIMIT,
    SUMMARY_TRIGGER_CHAR_LIMIT,
    SUMMARY_TRIGGER_TOKEN_LIMIT_ENABLED,
    SUMMARY_TRIGGER_TURN_LIMIT,
    SUMMARY_TRIGGER_TOKEN_LIMIT,
)
from src.core.telemetry import emit_event
from src.core.context.messages import (
    SUMMARY_MESSAGE_PREFIX,
    strip_context_messages,
)
from src.core.context.models import AgentContextState, TurnChunk
from src.core.context.budget import ContextWindowPlanner
from src.core.context.summary_executor import ContextSummaryExecutor
from src.core.context.summary_policy import SummaryPolicy
from src.core.llm.contracts import ModelProvider


class AgentContextManager:
    """Build bounded LLM inputs and compress old conversation turns."""

    def __init__(
        self,
        model_provider: ModelProvider,
        recent_turn_limit: int = RECENT_TURN_LIMIT,
        summary_trigger_turn_limit: int = SUMMARY_TRIGGER_TURN_LIMIT,
        summary_trigger_char_limit: int = SUMMARY_TRIGGER_CHAR_LIMIT,
        summary_trigger_token_limit_enabled: bool = SUMMARY_TRIGGER_TOKEN_LIMIT_ENABLED,
        summary_trigger_token_limit: int = SUMMARY_TRIGGER_TOKEN_LIMIT,
        summary_max_chars: int = SESSION_SUMMARY_MAX_CHARS,
        summary_source_char_limit: int = SUMMARY_SOURCE_CHAR_LIMIT,
        window_planner: ContextWindowPlanner | None = None,
        **legacy_limits,
    ) -> None:
        if "recent_message_limit" in legacy_limits:
            recent_turn_limit = int(legacy_limits.pop("recent_message_limit"))
        if "summary_trigger_message_limit" in legacy_limits:
            summary_trigger_turn_limit = int(
                legacy_limits.pop("summary_trigger_message_limit")
            )
        if legacy_limits:
            unknown = ", ".join(sorted(legacy_limits))
            raise TypeError(f"Unknown AgentContextManager limit(s): {unknown}")
        self.recent_turn_limit = recent_turn_limit
        self.summary_trigger_turn_limit = summary_trigger_turn_limit
        self.summary_trigger_char_limit = summary_trigger_char_limit
        self.summary_trigger_token_limit_enabled = summary_trigger_token_limit_enabled
        self.summary_trigger_token_limit = summary_trigger_token_limit
        # Compatibility for tests and older internal callers.
        self.recent_message_limit = recent_turn_limit
        self.summary_trigger_message_limit = summary_trigger_turn_limit
        self.summary_max_chars = summary_max_chars
        self.summary_source_char_limit = summary_source_char_limit
        self.window_planner = window_planner or ContextWindowPlanner(
            recent_turn_limit=recent_turn_limit,
            summary_trigger_token_limit_enabled=summary_trigger_token_limit_enabled,
            summary_trigger_token_limit=summary_trigger_token_limit,
            summary_max_chars=summary_max_chars,
        )
        self.summary_executor = ContextSummaryExecutor(
            model_provider=model_provider,
            summary_max_chars=summary_max_chars,
            summary_source_char_limit=summary_source_char_limit,
        )
        self.summary_policy = SummaryPolicy(
            turn_limit=summary_trigger_turn_limit,
            char_limit=summary_trigger_char_limit,
            token_limit_enabled=summary_trigger_token_limit_enabled,
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

        # The active context-window planner is the only component allowed to
        # evict Turns. Slicing here would hide unsummarized history.
        messages.extend(self._flatten_turns(state.recent_turns))
        messages.append(HumanMessage(content=user_input))
        return messages

    def update_after_turn(
        self,
        state: AgentContextState,
        final_messages: list,
        turn_index: int | None = None,
        force_summarize: bool = False,
        memory_context: str = "",
    ) -> AgentContextState:
        """Compatibility path that never evicts history before durable CAS."""
        if force_summarize:
            emit_event(
                "context_summary_deferred",
                "agent_context",
                "Forced context summary was deferred to durable window maintenance.",
                {"memory_context_supplied": bool(memory_context)},
            )
        return self.build_fast_state(state, final_messages, turn_index)

    def build_fast_state(
        self,
        state: AgentContextState,
        final_messages: list,
        turn_index: int | None = None,
    ) -> AgentContextState:
        """Build bounded committed context without invoking a summary model."""
        final_conversation_messages = self.extract_turn_messages(
            state,
            final_messages,
            turn_index,
        )
        turn = self._turn_from_messages(turn_index, final_conversation_messages)
        conversation_turns = [*state.recent_turns, turn]
        return AgentContextState(
            summary=state.summary,
            recent_turns=conversation_turns,
            context_tokens=state.context_tokens,
            context_window_id=state.context_window_id,
            summary_through_turn=state.summary_through_turn,
        )

    def plan_window(
        self,
        state: AgentContextState,
        *,
        fixed_messages: list | None = None,
    ):
        """Plan a complete-Turn suffix without mutating persisted context."""
        return self.window_planner.plan(
            state.recent_turns,
            fixed_messages=fixed_messages,
            summary=state.summary,
        )

    def should_summarize(self, messages: list, turn_count: int | None = None) -> bool:
        """Expose the summary policy to durable maintenance handlers."""
        return self.summary_policy.should_summarize_messages(
            messages,
            turn_count=turn_count,
        )

    def summarize_messages(self, previous_summary: str, messages: list, memory_context: str = "") -> str:
        """Create a derived summary outside the response critical path."""
        summary, _, _ = self._summarize_messages(previous_summary, messages, memory_context)
        return summary

    def summarize_messages_with_usage(
        self,
        previous_summary: str,
        messages: list,
        memory_context: str = "",
    ) -> tuple[str, int, int]:
        """Summarize all source messages and retain aggregate model usage."""
        return self._summarize_messages(previous_summary, messages, memory_context)

    def extract_turn_messages(
        self,
        state: AgentContextState,
        final_messages: list,
        turn_index: int | None = None,
    ) -> list:
        """Return only messages created by the current Turn.

        Synthetic summary and memory messages are removed first. This remains
        stable across checkpoint resumes even if injected memory changes.
        """
        conversation = strip_context_messages(final_messages)
        if turn_index is None:
            loaded_recent_count = min(len(state.recent_messages), self.recent_turn_limit)
            return conversation[loaded_recent_count:]
        loaded_before_current = 0
        loaded_current = 0
        for recent_turn in state.recent_turns:
            if recent_turn.turn_index == turn_index:
                loaded_current = len(recent_turn.messages)
            else:
                loaded_before_current += len(recent_turn.messages)
        offset = loaded_before_current + loaded_current
        if len(conversation) < offset:
            # Some direct unit callers pass only the current Turn instead of the
            # full graph message list. In that shape only same-Turn resume
            # messages can be skipped.
            offset = loaded_current
        return conversation[offset:]

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

    @staticmethod
    def _flatten_turns(turns: list[TurnChunk]) -> list:
        return [message for turn in turns for message in turn.messages]

    @staticmethod
    def _turn_from_messages(turn_index: int | None, messages: list) -> TurnChunk:
        return TurnChunk(0 if turn_index is None else int(turn_index), list(messages))
