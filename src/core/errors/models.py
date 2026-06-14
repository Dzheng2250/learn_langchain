"""Stable provider-error vocabulary shared by parsers and Agent execution."""

from dataclasses import dataclass
from enum import StrEnum


class ErrorCategory(StrEnum):
    """Provider-neutral categories used by execution policies."""

    CONTENT_REJECTED = "content_rejected"
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION = "authentication"
    RATE_LIMITED = "rate_limited"
    SERVICE_UNAVAILABLE = "service_unavailable"
    NETWORK = "network"
    UNKNOWN = "unknown"


class ErrorAction(StrEnum):
    """How the current Execution should react to a classified failure."""

    PAUSE = "pause"
    TERMINATE = "terminate"


@dataclass(frozen=True)
class ParsedProviderError:
    """Sanitized facts extracted from one provider-specific exception."""

    category: ErrorCategory
    provider: str = "unknown"
    provider_code: str = ""
    http_status: int | None = None


@dataclass(frozen=True)
class ErrorResolution:
    """Provider-neutral action and safe user-facing explanation."""

    category: ErrorCategory
    action: ErrorAction
    retryable: bool
    public_message: str
    provider: str = "unknown"
    provider_code: str = ""
    http_status: int | None = None

    def event_data(self) -> dict:
        """Return safe structured fields suitable for streaming and telemetry."""
        return {
            "error_category": self.category.value,
            "error_action": self.action.value,
            "retryable": self.retryable,
            "provider": self.provider,
            "provider_code": self.provider_code,
            "http_status": self.http_status,
        }
