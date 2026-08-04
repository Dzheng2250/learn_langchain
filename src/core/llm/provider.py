"""Concrete LangChain model provider implementations."""

from langchain_anthropic import ChatAnthropic
# Internal compatibility dependency: contract tests must catch changes when
# upgrading langchain-anthropic because no public converter exposes cache fields.
from langchain_anthropic.chat_models import convert_to_anthropic_tool

from src.config.settings import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_TOKENS,
    MODEL,
    PROMPT_CACHE_ENABLED,
    PROMPT_CACHE_MESSAGES,
    PROMPT_CACHE_SYSTEM,
    PROMPT_CACHE_TOOLS,
    PROMPT_CACHE_TTL,
)
from src.core.llm.contracts import (
    LlmConfigurationStatus,
    LlmPurpose,
    ModelConfiguration,
    ModelProvider,
)
from src.core.llm.prompt_cache import PromptCachePolicy, PromptCacheRunnable, PromptCacheSettings


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
        max_tokens: int | None = None,
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
            max_tokens=LLM_MAX_TOKENS if max_tokens is None else int(max_tokens),
            max_retries=0,
        )
        policy = PromptCachePolicy(
            PromptCacheSettings(
                enabled=PROMPT_CACHE_ENABLED,
                ttl=PROMPT_CACHE_TTL,
                cache_system=PROMPT_CACHE_SYSTEM,
                cache_tools=PROMPT_CACHE_TOOLS,
                cache_messages=PROMPT_CACHE_MESSAGES,
            )
        )
        formatted_tools = (
            [dict(convert_to_anthropic_tool(tool)) for tool in tools]
            if tools
            else None
        )
        runnable = (
            model.bind_tools(policy.apply_tools(formatted_tools))
            if formatted_tools
            else model
        )
        return PromptCacheRunnable(runnable, policy)


__all__ = [
    "AnthropicProvider",
    "LlmConfigurationStatus",
    "LlmPurpose",
    "ModelConfiguration",
    "ModelProvider",
]
