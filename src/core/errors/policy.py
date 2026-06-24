"""Provider-neutral policies that decide how classified errors affect execution."""

from typing import Protocol

from src.core.errors.models import (
    ErrorAction,
    ErrorCategory,
    ErrorResolution,
    ProviderErrorEnvelope,
)


class ErrorResolutionPolicy(Protocol):
    """Choose an execution action for sanitized provider error facts."""

    def resolve(self, error: ProviderErrorEnvelope) -> ErrorResolution:
        """Return a safe user-facing resolution."""


class DefaultErrorResolutionPolicy:
    """Terminate deterministic request failures and pause transient failures."""

    _TERMINAL_MESSAGES = {
        ErrorCategory.CONTENT_REJECTED: (
            "The model provider rejected the request content. "
            "This turn was terminated; revise the input and continue."
        ),
        ErrorCategory.INVALID_REQUEST: (
            "The model provider rejected this request as invalid. "
            "This turn was terminated; revise the input and continue."
        ),
        ErrorCategory.AUTHENTICATION: (
            "The model provider rejected the configured credentials. "
            "This turn was terminated; update the model configuration before retrying."
        ),
        ErrorCategory.USAGE_LIMIT: "The configured account has reached its usage limit.",
        ErrorCategory.QUOTA_EXHAUSTED: "The configured provider account has exhausted its quota.",
        ErrorCategory.CONTEXT_LENGTH_EXCEEDED: "The model context window was exceeded.",
        ErrorCategory.MODEL_NOT_FOUND: "The configured model is unavailable or does not exist.",
    }
    _TRANSIENT_MESSAGES = {
        ErrorCategory.RATE_LIMITED: (
            "The model provider rate-limited the request. "
            "The execution was paused and can be resumed later."
        ),
        ErrorCategory.SERVICE_OVERLOADED: "The model provider is temporarily overloaded.",
        ErrorCategory.TIMEOUT: "The model provider request timed out.",
        ErrorCategory.CONNECTION_FAILED: "The model provider connection failed.",
        ErrorCategory.STREAM_INTERRUPTED: "The model response stream was interrupted.",
        ErrorCategory.SERVICE_UNAVAILABLE: (
            "The model provider is temporarily unavailable. "
            "The execution was paused and can be resumed later."
        ),
        ErrorCategory.NETWORK: (
            "The model provider could not be reached. "
            "The execution was paused and can be resumed later."
        ),
        ErrorCategory.UNKNOWN: (
            "Model execution failed unexpectedly. "
            "The execution was paused so its state can be inspected."
        ),
    }

    def resolve(self, error: ProviderErrorEnvelope) -> ErrorResolution:
        """Return deterministic terminal or recoverable pause behavior."""
        terminal = error.category in self._TERMINAL_MESSAGES
        return ErrorResolution(
            category=error.category,
            action=ErrorAction.TERMINATE if terminal else ErrorAction.PAUSE,
            retryable=not terminal,
            public_message=(
                self._TERMINAL_MESSAGES[error.category]
                if terminal
                else self._TRANSIENT_MESSAGES[error.category]
            ),
            provider=error.provider,
            provider_code=error.provider_code,
            http_status=error.http_status,
            request_id=error.request_id,
            retry_after_seconds=error.retry_after_seconds,
            retry_at=error.retry_at,
            source=error.source,
        )
