"""Provider-neutral contracts for all Core LLM workloads."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class LlmPurpose(StrEnum):
    """Stable workload labels attached to provider-created model clients."""

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
    """Readiness check used before constructing Workspace runtimes."""

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
        max_tokens: int | None = None,
    ):
        """Return a configured model, optionally bound to tools."""
