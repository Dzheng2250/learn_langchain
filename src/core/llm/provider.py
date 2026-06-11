"""Central model construction for all Core LLM workloads."""

import os
from enum import StrEnum
from typing import Protocol

from langchain_openai import ChatOpenAI

from src.config.settings import MODEL


class LlmPurpose(StrEnum):
    PARENT_AGENT = "parent_agent"
    SUBAGENT = "subagent"
    CONTEXT_SUMMARY = "context_summary"
    MEMORY_EXTRACTION = "memory_extraction"
    FILE_SUMMARY = "file_summary"


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
        self.api_key = api_key if api_key is not None else os.getenv("ALIYUN_API_KEY")
        self.base_url = base_url if base_url is not None else os.getenv("ALIYUN_BASE_URL")

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
