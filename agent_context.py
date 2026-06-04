import os
from dataclasses import dataclass, field

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from agent_config import (
    MODEL,
    RECENT_MESSAGE_LIMIT,
    SESSION_SUMMARY_MAX_CHARS,
    SUMMARY_TRIGGER_CHAR_LIMIT,
    SUMMARY_SOURCE_CHAR_LIMIT,
    SUMMARY_TRIGGER_MESSAGE_LIMIT,
)
from agent_debug import debug_print, format_message
from agent_hooks import emit_event, event_span, record_error


SUMMARY_MESSAGE_PREFIX = "Conversation context summary:"
MEMORY_MESSAGE_PREFIX = "Relevant long-term memory:"


@dataclass
class AgentContextState:
    """Compact conversation state kept outside the LangGraph message history."""

    summary: str = ""
    recent_messages: list = field(default_factory=list)


class AgentContextManager:
    """Build bounded LLM inputs and compress old conversation turns."""

    def __init__(
        self,
        recent_message_limit: int = RECENT_MESSAGE_LIMIT,
        summary_trigger_message_limit: int = SUMMARY_TRIGGER_MESSAGE_LIMIT,
        summary_trigger_char_limit: int = SUMMARY_TRIGGER_CHAR_LIMIT,
        summary_max_chars: int = SESSION_SUMMARY_MAX_CHARS,
        summary_source_char_limit: int = SUMMARY_SOURCE_CHAR_LIMIT,
    ) -> None:
        self.recent_message_limit = recent_message_limit
        self.summary_trigger_message_limit = summary_trigger_message_limit
        self.summary_trigger_char_limit = summary_trigger_char_limit
        self.summary_max_chars = summary_max_chars
        self.summary_source_char_limit = summary_source_char_limit

    def build_input_messages(
        self,
        state: AgentContextState,
        user_input: str,
        extra_system_messages: list | None = None,
    ) -> list:
        """Build one bounded graph input from compact context state."""
        messages = []
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
    ) -> AgentContextState:
        """Update compact context after one graph execution."""
        final_conversation_messages = self._strip_context_summary_messages(final_messages)
        unsent_previous_messages = state.recent_messages[:-self.recent_message_limit]
        conversation_messages = [*unsent_previous_messages, *final_conversation_messages]

        should_summarize = force_summarize or self._should_summarize(conversation_messages)
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
            summary = self._summarize_messages(state.summary, old_messages)
        except Exception as exc:
            record_error(
                "agent_context",
                "context_summary",
                exc,
                "Context summary failed.",
                {"old_message_count": len(old_messages)},
                event_type="context_summary_failed",
            )
            raise

        return AgentContextState(summary=summary, recent_messages=recent_messages)

    def _should_summarize(self, messages: list) -> bool:
        """Return whether message volume is large enough to compress."""
        if len(messages) > self.summary_trigger_message_limit:
            return True

        total_chars = 0
        for message in messages:
            content = getattr(message, "content", "")
            if isinstance(content, str):
                total_chars += len(content)
            else:
                total_chars += len(repr(content))

        return total_chars > self.summary_trigger_char_limit

    def _strip_context_summary_messages(self, messages: list) -> list:
        """Remove synthetic summary messages before persisting recent history."""
        stripped = []
        for message in messages:
            if (
                message.__class__.__name__ == "SystemMessage"
                and isinstance(message.content, str)
                and (
                    message.content.startswith(SUMMARY_MESSAGE_PREFIX)
                    or message.content.startswith(MEMORY_MESSAGE_PREFIX)
                )
            ):
                continue
            stripped.append(message)
        return stripped

    def _summarize_messages(self, previous_summary: str, messages: list) -> str:
        """Compress older messages into a structured session summary."""
        source = self._format_messages_for_summary(messages)
        if len(source) > self.summary_source_char_limit:
            source = source[-self.summary_source_char_limit:]

        llm = self._create_summary_llm()
        with event_span(
            "context_summary_llm",
            "agent_context",
            payload={"source_chars": len(source), "message_count": len(messages)},
        ):
            response = llm.invoke([
                SystemMessage(
                    content=(
                        "You maintain compact context for a coding agent. "
                        "Update the session summary using only the supplied prior summary "
                        "and older conversation messages. Preserve concrete facts, decisions, "
                        "file names, current architecture, open issues, user preferences, and "
                        "constraints. Remove transient wording and redundant tool output. "
                        "Use concise Markdown sections."
                    )
                ),
                HumanMessage(
                    content=(
                        f"Previous summary:\n{previous_summary or '(none)'}\n\n"
                        f"Older messages to compress:\n{source}\n\n"
                        f"Return an updated summary under {self.summary_max_chars} characters."
                    )
                ),
            ])

        summary = response.content.strip()
        if len(summary) > self.summary_max_chars:
            summary = summary[:self.summary_max_chars] + "\n... summary truncated ..."

        debug_print("CONTEXT SUMMARY UPDATED", summary)
        emit_event(
            "context_summarized",
            "agent_context",
            "Context summary updated.",
            {"summary_chars": len(summary), "compressed_messages": len(messages)},
        )
        return summary

    def _format_messages_for_summary(self, messages: list) -> str:
        """Format messages for summarization with bounded per-message content."""
        formatted = []
        for index, message in enumerate(messages, start=1):
            text = format_message(message)
            if len(text) > 1200:
                text = text[:1200] + "\n... message truncated ..."
            formatted.append(f"[{index}]\n{text}")
        return "\n\n".join(formatted)

    def _create_summary_llm(self) -> ChatOpenAI:
        """Create a non-streaming model for context summarization."""
        load_dotenv()
        return ChatOpenAI(
            model=MODEL,
            api_key=os.getenv("ALIYUN_API_KEY"),
            base_url=os.getenv("ALIYUN_BASE_URL"),
            temperature=0,
            streaming=False,
        )
