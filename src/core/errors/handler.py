"""Facade combining provider-specific parsing with provider-neutral policy."""

from src.core.errors.models import ErrorResolution
from src.core.errors.parsers import ProviderErrorParserRegistry
from src.core.errors.policy import DefaultErrorResolutionPolicy, ErrorResolutionPolicy


class ProviderErrorHandler:
    """Classify exceptions through replaceable parsers and resolution policy."""

    def __init__(
        self,
        parser_registry: ProviderErrorParserRegistry | None = None,
        policy: ErrorResolutionPolicy | None = None,
    ) -> None:
        self.parser_registry = parser_registry or ProviderErrorParserRegistry()
        self.policy = policy or DefaultErrorResolutionPolicy()

    def resolve(self, exc: Exception) -> ErrorResolution:
        """Return a safe, provider-neutral execution resolution."""
        return self.policy.resolve(self.parser_registry.parse(exc))
