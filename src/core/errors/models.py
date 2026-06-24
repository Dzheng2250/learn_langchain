"""Stable provider-error vocabulary shared by parsers and Agent execution."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ErrorCategory(StrEnum):
    """Provider-neutral categories used by execution policies."""

    CONTENT_REJECTED = "content_rejected"
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION = "authentication"
    RATE_LIMITED = "rate_limited"
    USAGE_LIMIT = "usage_limit"
    QUOTA_EXHAUSTED = "quota_exhausted"
    SERVICE_OVERLOADED = "service_overloaded"
    SERVICE_UNAVAILABLE = "service_unavailable"
    TIMEOUT = "timeout"
    CONNECTION_FAILED = "connection_failed"
    STREAM_INTERRUPTED = "stream_interrupted"
    CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"
    MODEL_NOT_FOUND = "model_not_found"
    NETWORK = "network"
    UNKNOWN = "unknown"


class ErrorAction(StrEnum):
    """How the current Execution should react to a classified failure."""

    PAUSE = "pause"
    TERMINATE = "terminate"


@dataclass(frozen=True)
class ProviderErrorEnvelope:
    """Sanitized provider-neutral facts extracted from one exception chain."""

    category: ErrorCategory
    provider: str = "unknown"
    provider_code: str = ""
    http_status: int | None = None
    request_id: str = ""
    retry_after_seconds: float | None = None
    retry_at: datetime | None = None
    retryable_hint: bool | None = None
    source: str = "unknown"


ParsedProviderError = ProviderErrorEnvelope


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
    request_id: str = ""
    retry_after_seconds: float | None = None
    retry_at: datetime | None = None
    source: str = "unknown"

    def event_data(self) -> dict:
        """Return safe structured fields suitable for streaming and telemetry."""
        return {
            "error_category": self.category.value,
            "error_action": self.action.value,
            "retryable": self.retryable,
            "provider": self.provider,
            "provider_code": self.provider_code,
            "http_status": self.http_status,
            "request_id": self.request_id,
            "retry_after_seconds": self.retry_after_seconds,
            "retry_at": self.retry_at.isoformat() if self.retry_at else None,
            "error_source": self.source,
        }
