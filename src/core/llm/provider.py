"""Central model construction and configuration checks for Core LLM workloads."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from langchain_openai import ChatOpenAI

from src.config.settings import LLM_API_KEY, LLM_BASE_URL, MODEL


class LlmPurpose(StrEnum):
    PARENT_AGENT = "parent_agent"
    SUBAGENT = "subagent"
    CONTEXT_SUMMARY = "context_summary"
    MEMORY_EXTRACTION = "memory_extraction"
    FILE_SUMMARY = "file_summary"


@dataclass(frozen=True)
class LlmConfigurationStatus:
    """Describe whether Core has enough configuration to call the model."""

    configured: bool
    missing: tuple[str, ...] = ()


class ModelConfiguration(Protocol):
    def configuration_status(self) -> LlmConfigurationStatus:
        """Return model readiness without performing a network request."""


class ModelProvider(Protocol):
    """Create chat models without exposing vendor construction to consumers."""

    def create_chat_model(
        self,
        purpose: LlmPurpose,
        *,
        streaming: bool = False,
        temperature: float = 0,
        tools: list | None = None,
    ):
        """Return a configured model, optionally bound to tools."""


class OpenAICompatibleProvider:
    """Build ChatOpenAI clients for an OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        model: str = MODEL,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key if api_key is not None else LLM_API_KEY
        self.base_url = base_url if base_url is not None else LLM_BASE_URL or None

    def configuration_status(self) -> LlmConfigurationStatus:
        """Check required local configuration without contacting the provider."""
        missing = () if str(self.api_key or "").strip() else ("LEARN_AGENT_LLM_API_KEY",)
        return LlmConfigurationStatus(configured=not missing, missing=missing)

    def create_chat_model(
        self,
        purpose: LlmPurpose,
        *,
        streaming: bool = False,
        temperature: float = 0,
        tools: list | None = None,
    ):
        model = ChatOpenAI(
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
            temperature=temperature,
            streaming=streaming,
            metadata={"purpose": purpose.value},
        )
        return model.bind_tools(tools) if tools else model
