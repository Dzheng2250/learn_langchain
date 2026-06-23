"""Extract and classify provider failures without binding Core to one vendor."""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
from typing import Protocol

from src.core.errors.models import ErrorCategory, ProviderErrorEnvelope

_TEXT_LIMIT = 20_000
_CHAIN_LIMIT = 6
_RETRY_PATTERN = re.compile(
    r"(?i)(?:try again|retry)\s+(?:in|after)\s*(\d+(?:\.\d+)?)\s*"
    r"(ms|milliseconds?|s|sec(?:ond)?s?|m|minutes?)"
)


class ProviderErrorParser(Protocol):
    """Translate an exception into provider-neutral facts when recognized."""

    def parse(self, exc: Exception) -> ProviderErrorEnvelope | None: ...


class ProviderErrorAdapter(Protocol):
    """Optionally enrich generic facts with vendor aliases or metadata."""

    def enrich(
        self, envelope: ProviderErrorEnvelope, exc: Exception
    ) -> ProviderErrorEnvelope: ...


class ProviderErrorParserRegistry:
    """Run generic parsers first, then isolate optional adapter failures."""

    def __init__(
        self,
        parsers: Iterable[ProviderErrorParser] | None = None,
        adapters: Iterable[ProviderErrorAdapter] | None = None,
    ) -> None:
        self.parsers = tuple(
            (GenericProviderErrorParser(),) if parsers is None else parsers
        )
        self.adapters = tuple(adapters or ())

    def parse(self, exc: Exception) -> ProviderErrorEnvelope:
        envelope = None
        for parser in self.parsers:
            try:
                envelope = parser.parse(exc)
            except Exception:
                continue
            if envelope is not None:
                break
        envelope = envelope or ProviderErrorEnvelope(ErrorCategory.UNKNOWN)
        for adapter in self.adapters:
            try:
                envelope = adapter.enrich(envelope, exc)
            except Exception:
                continue
        return envelope


class GenericProviderErrorParser:
    """Classify common SDK, HTTP, JSON and text error representations."""

    def parse(self, exc: Exception) -> ProviderErrorEnvelope:
        chain = tuple(_exception_chain(exc))
        status = next(
            (value for item in chain if (value := _http_status(item)) is not None),
            None,
        )
        body = next(
            (value for item in chain if (value := _exception_body(item))), {}
        )
        headers = next(
            (value for item in chain if (value := _response_headers(item))), {}
        )
        text = "\n".join(_exception_text(item) for item in chain)[:_TEXT_LIMIT]
        error = body.get("error") if isinstance(body.get("error"), Mapping) else body
        code = _first_text(
            error.get("code") if isinstance(error, Mapping) else None,
            error.get("type") if isinstance(error, Mapping) else None,
            body.get("error_code"),
        )
        request_id = _first_text(
            headers.get("x-request-id"),
            headers.get("request-id"),
            body.get("request_id"),
            error.get("request_id") if isinstance(error, Mapping) else None,
        )
        retry_after, retry_at, retry_source = _retry_hint(
            headers, body, error, text
        )
        category, source, retryable_hint = _classify(chain, status, code, text)
        if retry_source:
            source = f"{source}+{retry_source}"
        return ProviderErrorEnvelope(
            category=category,
            provider="openai_compatible" if status is not None or body else "unknown",
            provider_code=code,
            http_status=status,
            request_id=request_id,
            retry_after_seconds=retry_after,
            retry_at=retry_at,
            retryable_hint=retryable_hint,
            source=source,
        )


class OpenAICompatibleErrorParser(GenericProviderErrorParser):
    """Compatibility name for the generic OpenAI-compatible parser."""


class AliyunErrorParser:
    """Legacy content-filter parser retained as an optional extension."""

    _CONTENT_CODES = frozenset({"data_inspection_failed", "inappropriate_content"})

    def parse(self, exc: Exception) -> ProviderErrorEnvelope | None:
        envelope = GenericProviderErrorParser().parse(exc)
        if envelope.provider_code.casefold() not in self._CONTENT_CODES:
            return None
        return replace(
            envelope,
            category=ErrorCategory.CONTENT_REJECTED,
            provider="aliyun",
            retryable_hint=False,
            source="provider_adapter",
        )


def _classify(chain, status: int | None, code: str, text: str):
    normalized = f"{code} {text}".casefold()
    names = " ".join(type(item).__name__.casefold() for item in chain)
    code_groups = (
        (ErrorCategory.CONTENT_REJECTED, False, ("data_inspection_failed", "inappropriate_content", "content_filter", "content_policy")),
        (ErrorCategory.CONTEXT_LENGTH_EXCEEDED, False, ("context_length_exceeded", "context_too_long", "maximum context length")),
        (ErrorCategory.QUOTA_EXHAUSTED, False, ("insufficient_quota", "quota_exceeded", "credits_depleted")),
        (ErrorCategory.USAGE_LIMIT, False, ("usage_limit_reached", "usage_not_included", "usage_exceeded")),
        (ErrorCategory.MODEL_NOT_FOUND, False, ("model_not_found", "unknown_model", "model does not exist")),
    )
    for category, retryable, aliases in code_groups:
        if _contains(normalized, *aliases):
            return category, "provider_code", retryable
    if status == 429:
        return ErrorCategory.RATE_LIMITED, "http_status", True
    if status == 408 or "timeout" in names or "timed out" in normalized:
        return ErrorCategory.TIMEOUT, "exception_type" if status is None else "http_status", True
    if _contains(names, "connection", "connecterror", "network") or _contains(normalized, "connection reset", "connection refused", "dns failure"):
        return ErrorCategory.CONNECTION_FAILED, "exception_type", True
    if _contains(names, "stream", "incomplete") or _contains(normalized, "stream disconnected", "response stream"):
        return ErrorCategory.STREAM_INTERRUPTED, "exception_type", True
    if status in {503, 529} or _contains(normalized, "server_is_overloaded", "slow_down", "server_busy", "overloaded"):
        return ErrorCategory.SERVICE_OVERLOADED, "http_status" if status else "provider_code", True
    if status in {500, 502, 504}:
        return ErrorCategory.SERVICE_UNAVAILABLE, "http_status", True
    if status in {401, 403}:
        return ErrorCategory.AUTHENTICATION, "http_status", False
    if status == 404:
        return ErrorCategory.MODEL_NOT_FOUND, "http_status", False
    if status in {400, 409, 410, 422}:
        return ErrorCategory.INVALID_REQUEST, "http_status", False
    return ErrorCategory.UNKNOWN, "unknown", None


def _retry_hint(headers, body, error, text):
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is not None:
        seconds, retry_at = _parse_retry_after(raw)
        if seconds is not None or retry_at is not None:
            return seconds, retry_at, "retry_after_header"
    for mapping in (error, body):
        if not isinstance(mapping, Mapping):
            continue
        raw = mapping.get("retry_after") or mapping.get("retry_after_seconds")
        if raw is not None:
            try:
                return max(0.0, float(raw)), None, "retry_after_body"
            except (TypeError, ValueError):
                pass
        parsed = _parse_datetime(mapping.get("resets_at") or mapping.get("retry_at"))
        if parsed is not None:
            return None, parsed, "retry_at_body"
    match = _RETRY_PATTERN.search(text)
    if match:
        amount = float(match.group(1))
        unit = match.group(2).casefold()
        if unit.startswith("ms") or unit.startswith("millisecond"):
            amount /= 1000
        elif unit.startswith("m"):
            amount *= 60
        return amount, None, "retry_after_text"
    return None, None, ""


def _parse_retry_after(value):
    try:
        return max(0.0, float(value)), None
    except (TypeError, ValueError):
        parsed = _parse_datetime(value)
        if parsed is None:
            return None, None
        return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds()), parsed


def _parse_datetime(value):
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        text = str(value).strip()
        if text.replace(".", "", 1).isdigit():
            return datetime.fromtimestamp(float(text), tz=timezone.utc)
        parsed = parsedate_to_datetime(text) if "," in text else datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _exception_chain(exc):
    seen = set()
    current = exc
    for _ in range(_CHAIN_LIMIT):
        if current is None or id(current) in seen:
            return
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _http_status(exc):
    value = getattr(exc, "status_code", None)
    if value is None:
        value = getattr(getattr(exc, "response", None), "status_code", None)
    if value is None:
        match = re.search(r"\b(?:Error code|HTTP status|status code):\s*(\d{3})\b", _exception_text(exc))
        value = match.group(1) if match else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _exception_body(exc):
    response = getattr(exc, "response", None)
    for value in (getattr(exc, "body", None), getattr(exc, "error", None), getattr(response, "json", None)):
        if callable(value):
            try:
                value = value()
            except Exception:
                continue
        if isinstance(value, Mapping):
            return dict(value)
    match = re.search(r"(\{''error'':\s*\{.*\}\})", _exception_text(exc))
    if match:
        try:
            value = ast.literal_eval(match.group(1))
            return value if isinstance(value, dict) else {}
        except (SyntaxError, ValueError):
            pass
    return {}


def _response_headers(exc):
    headers = getattr(getattr(exc, "response", None), "headers", None) or getattr(exc, "headers", None)
    return dict(headers) if isinstance(headers, Mapping) else {}


def _exception_text(exc):
    return str(exc)[:_TEXT_LIMIT]


def _first_text(*values):
    return next((str(value).strip() for value in values if value not in (None, "")), "")


def _contains(value: str, *parts: str) -> bool:
    return any(part in value for part in parts)
