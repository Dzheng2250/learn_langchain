"""Provider-neutral policies that decide how classified errors affect execution."""

from typing import Protocol

from src.core.errors.models import (
    ErrorAction,
    ErrorCategory,
    ErrorResolution,
    ParsedProviderError,
)


class ErrorResolutionPolicy(Protocol):
    """Choose an execution action for sanitized provider error facts."""

    def resolve(self, error: ParsedProviderError) -> ErrorResolution:
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
    }
    _TRANSIENT_MESSAGES = {
        ErrorCategory.RATE_LIMITED: (
            "The model provider rate-limited the request. "
            "The execution was paused and can be resumed later."
        ),
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

    def resolve(self, error: ParsedProviderError) -> ErrorResolution:
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
        )
