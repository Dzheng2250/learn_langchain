import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from src.core.common.debug import debug_print, format_message
from src.config.settings import MEMORY_EXTRACT_SOURCE_CHAR_LIMIT
from src.core.hooks.events import event_span
from src.core.llm.provider import LlmPurpose, ModelProvider, OpenAICompatibleProvider


class MemoryCandidateExtractor:
    """Extract durable memory candidates from completed conversation turns."""

    def __init__(self, model_provider: ModelProvider | None = None) -> None:
        self.model_provider = model_provider or OpenAICompatibleProvider()

    def format_messages(self, messages: list) -> str:
        """Format and bound source messages before sending them to the LLM."""
        formatted = []
        for index, message in enumerate(messages, start=1):
            text = format_message(message)
            if len(text) > 1200:
                text = text[:1200] + "\n... message truncated ..."
            formatted.append(f"[{index}]\n{text}")

        source = "\n\n".join(formatted)
        if len(source) > MEMORY_EXTRACT_SOURCE_CHAR_LIMIT:
            source = source[-MEMORY_EXTRACT_SOURCE_CHAR_LIMIT:]
        return source

    def extract(self, source: str) -> list[dict]:
        """Call the memory LLM and return validated candidate dictionaries."""
        llm = self._create_llm()
        with event_span(
            "memory_candidate_extract",
            "agent_memory",
            payload={"source_chars": len(source)},
        ):
            response = llm.invoke(
                [
                    SystemMessage(
                        content=(
                            "Extract durable long-term memories for a local coding agent. "
                            "Return strict JSON only: an array of objects with keys "
                            "kind, content, tags, importance, confidence. "
                            "Only include stable user preferences, project facts, architecture "
                            "decisions, task state, or reusable troubleshooting notes. "
                            "If the user explicitly asks to remember something, extract the "
                            "thing they asked to remember as a durable memory unless it is "
                            "sensitive or unsafe. "
                            "Do not include secrets, API keys, passwords, .env values, transient "
                            "tool output, or generic conversation filler. "
                            "If nothing should be remembered, return []."
                        )
                    ),
                    HumanMessage(content=f"Conversation turn:\n{source}"),
                ]
            )

        content = str(response.content).strip()
        try:
            parsed = json.loads(self._strip_json_fence(content))
        except json.JSONDecodeError:
            debug_print("MEMORY EXTRACT PARSE FAILED", content)
            return []

        if not isinstance(parsed, list):
            return []

        debug_print("MEMORY EXTRACT", json.dumps(parsed, ensure_ascii=False, indent=2))
        return [item for item in parsed if isinstance(item, dict)]

    def looks_sensitive(self, content: str) -> bool:
        """Return whether extracted content appears to contain secrets."""
        lower = content.lower()
        sensitive_terms = [
            "api_key",
            "apikey",
            "password",
            "passwd",
            "secret",
            "token",
            ".env",
            "authorization",
        ]
        return any(term in lower for term in sensitive_terms)

    def _strip_json_fence(self, content: str) -> str:
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        return content.strip()

    def _create_llm(self):
        return self.model_provider.create_chat_model(
            LlmPurpose.MEMORY_EXTRACTION,
            temperature=0,
            streaming=False,
        )
