"""Concrete LangChain model provider implementations."""

from langchain_anthropic import ChatAnthropic

from src.config.settings import (
    LLM_API_KEY,
    LLM_BASE_URL,
    MODEL,
)
from src.core.llm.contracts import (
    LlmConfigurationStatus,
    LlmPurpose,
    ModelConfiguration,
    ModelProvider,
)


class AnthropicProvider:
    """Build ChatAnthropic clients for the default Anthropic Messages API."""

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
        missing = tuple(
            name
            for name, value in (
                ("LEARN_AGENT_LLM_API_KEY", self.api_key),
                ("LEARN_AGENT_MODEL", self.model),
            )
            if not str(value or "").strip()
        )
        return LlmConfigurationStatus(configured=not missing, missing=missing)

    def create_chat_model(
        self,
        purpose: LlmPurpose,
        *,
        streaming: bool = False,
        temperature: float = 0,
        tools: list | None = None,
    ):
        """Create a ChatAnthropic client and optionally bind the supplied tools."""
        status = self.configuration_status()
        if not status.configured:
            raise RuntimeError(
                "LLM configuration is incomplete: " + ", ".join(status.missing)
            )
        model = ChatAnthropic(
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
            temperature=temperature,
            streaming=streaming,
            metadata={"purpose": purpose.value},
            max_retries=0,
        )
        return model.bind_tools(tools) if tools else model


__all__ = [
    "AnthropicProvider",
    "LlmConfigurationStatus",
    "LlmPurpose",
    "ModelConfiguration",
    "ModelProvider",
]
