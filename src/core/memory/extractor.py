"""LLM-backed extraction of durable memory candidates from completed Turns."""

import json
import re

from src.core.common.content import message_content_text
from src.core.common.debug import debug_print, format_message
from src.config.settings import MEMORY_EXTRACT_SOURCE_CHAR_LIMIT
from src.core.telemetry import event_span
from src.core.llm.contracts import LlmPurpose, ModelProvider
from src.core.prompts import build_memory_extraction_messages


MEMORY_SOURCE_MESSAGE_PREVIEW_CHARS = 1200


class MemoryCandidateExtractor:
    """Extract durable memory candidates from completed conversation turns."""

    def __init__(self, model_provider: ModelProvider) -> None:
        self.model_provider = model_provider

    def format_messages(self, messages: list) -> str:
        """Format and bound source messages before sending them to the LLM."""
        formatted = []
        for index, message in enumerate(messages, start=1):
            text = format_message(message)
            if len(text) > MEMORY_SOURCE_MESSAGE_PREVIEW_CHARS:
                text = text[:MEMORY_SOURCE_MESSAGE_PREVIEW_CHARS] + "\n... message truncated ..."
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
            response = llm.invoke(build_memory_extraction_messages(source))

        content = message_content_text(response).strip()
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
        """Remove an optional Markdown JSON fence before parsing model output."""
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        return content.strip()

    def _create_llm(self):
        """Create the deterministic model used only for memory extraction."""
        return self.model_provider.create_chat_model(
            LlmPurpose.MEMORY_EXTRACTION,
            temperature=0,
            streaming=False,
        )
