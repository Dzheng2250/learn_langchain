"""Extensible parsers that translate provider exceptions into stable facts."""

from collections.abc import Iterable
import ast
import re
from typing import Protocol

from src.core.errors.models import ErrorCategory, ParsedProviderError


class ProviderErrorParser(Protocol):
    """Parse one provider-specific exception without deciding its resolution."""

    def parse(self, exc: Exception) -> ParsedProviderError | None:
        """Return sanitized provider facts when this parser recognizes the error."""


class ProviderErrorParserRegistry:
    """Try registered parsers in priority order, then return an unknown error."""

    def __init__(self, parsers: Iterable[ProviderErrorParser] | None = None) -> None:
        self.parsers = tuple(
            (
                AliyunErrorParser(),
                OpenAICompatibleErrorParser(),
            )
            if parsers is None
            else parsers
        )

    def parse(self, exc: Exception) -> ParsedProviderError:
        """Return the first recognized provider error without leaking raw payloads."""
        for parser in self.parsers:
            try:
                parsed = parser.parse(exc)
            except Exception:
                # A vendor adapter must never replace the original model
                # failure with its own parsing failure.
                continue
            if parsed is not None:
                return parsed
        return ParsedProviderError(ErrorCategory.UNKNOWN)


class AliyunErrorParser:
    """Recognize Model Studio error codes exposed through OpenAI-compatible clients."""

    _CONTENT_CODES = frozenset({"data_inspection_failed", "inappropriate_content"})

    def parse(self, exc: Exception) -> ParsedProviderError | None:
        body = _exception_body(exc)
        error = body.get("error") if isinstance(body.get("error"), dict) else body
        code = str(error.get("code") or error.get("type") or "").strip()
        if not code:
            text = _exception_text(exc)
            code = next((item for item in self._CONTENT_CODES if item in text), "")
        if not code:
            return None
        status = _http_status(exc)
        if code in self._CONTENT_CODES:
            category = ErrorCategory.CONTENT_REJECTED
        elif status in {401, 403}:
            category = ErrorCategory.AUTHENTICATION
        elif status == 429:
            category = ErrorCategory.RATE_LIMITED
        elif status == 400:
            category = ErrorCategory.INVALID_REQUEST
        elif status is not None and status >= 500:
            category = ErrorCategory.SERVICE_UNAVAILABLE
        else:
            return None
        return ParsedProviderError(category, "aliyun", code, status)


class OpenAICompatibleErrorParser:
    """Classify common HTTP failures from any OpenAI-compatible endpoint."""

    def parse(self, exc: Exception) -> ParsedProviderError | None:
        status = _http_status(exc)
        if status is None:
            name = exc.__class__.__name__.lower()
            if any(fragment in name for fragment in ("timeout", "connection", "network")):
                return ParsedProviderError(ErrorCategory.NETWORK, "openai_compatible")
            return None

        body = _exception_body(exc)
        error = body.get("error") if isinstance(body.get("error"), dict) else body
        code = str(error.get("code") or error.get("type") or "").strip()
        if status in {401, 403}:
            category = ErrorCategory.AUTHENTICATION
        elif status == 429:
            category = ErrorCategory.RATE_LIMITED
        elif status == 400:
            category = ErrorCategory.INVALID_REQUEST
        elif status >= 500:
            category = ErrorCategory.SERVICE_UNAVAILABLE
        else:
            return None
        return ParsedProviderError(category, "openai_compatible", code, status)


def _http_status(exc: Exception) -> int | None:
    """Extract an HTTP status from common SDK exception shapes."""
    value = getattr(exc, "status_code", None)
    if value is None:
        value = getattr(getattr(exc, "response", None), "status_code", None)
    if value is None:
        match = re.search(
            r"\b(?:Error code|HTTP status|status code):\s*(\d{3})\b",
            _exception_text(exc),
        )
        value = match.group(1) if match else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _exception_body(exc: Exception) -> dict:
    """Extract a structured error body without evaluating arbitrary text."""
    for value in (
        getattr(exc, "body", None),
        getattr(exc, "error", None),
        getattr(getattr(exc, "response", None), "json", None),
    ):
        if callable(value):
            try:
                value = value()
            except Exception:
                continue
        if isinstance(value, dict):
            return value

    # Some OpenAI-compatible SDK exceptions only expose a repr-like dictionary.
    match = re.search(r"(\{'error':\s*\{.*\}\})", _exception_text(exc))
    if match:
        try:
            value = ast.literal_eval(match.group(1))
            return value if isinstance(value, dict) else {}
        except (SyntaxError, ValueError):
            pass
    return {}


def _exception_text(exc: Exception, limit: int = 20_000) -> str:
    """Bound exception text before fallback parsing."""
    return str(exc)[:limit]
