"""Normalize exact token usage reported by model providers."""

from __future__ import annotations

from typing import Any


_INPUT_KEYS = ("input_tokens", "prompt_tokens")
_OUTPUT_KEYS = ("output_tokens", "completion_tokens")


def message_usage(message: Any) -> dict[str, int | None]:
    """Return normalized usage from one completed LangChain message."""
    candidates: list[dict] = []
    usage_metadata = getattr(message, "usage_metadata", None)
    if isinstance(usage_metadata, dict):
        candidates.append(usage_metadata)
    response_metadata = getattr(message, "response_metadata", None)
    if isinstance(response_metadata, dict):
        for key in ("usage", "token_usage"):
            value = response_metadata.get(key)
            if isinstance(value, dict):
                candidates.append(value)
        candidates.append(response_metadata)
    return normalize_usage(candidates)


def response_usage(response: Any) -> dict[str, int | None]:
    """Return normalized usage from the first message in an LLM result."""
    generations = getattr(response, "generations", None) or []
    if not generations or not generations[0]:
        return normalize_usage([])
    message = getattr(generations[0][0], "message", None)
    return message_usage(message)


def normalize_usage(candidates: list[dict]) -> dict[str, int | None]:
    """Merge provider usage dictionaries without estimating missing values."""
    input_tokens = _first_non_negative(candidates, _INPUT_KEYS)
    output_tokens = _first_non_negative(candidates, _OUTPUT_KEYS)
    total_tokens = _first_non_negative(candidates, ("total_tokens",))
    if input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cache_creation_input_tokens": _first_non_negative(
            candidates, ("cache_creation_input_tokens",)
        ),
        "cache_read_input_tokens": _first_non_negative(
            candidates, ("cache_read_input_tokens",)
        ),
    }


def context_tokens(usage: dict[str, Any] | None) -> int:
    """Return exact latest-call input plus output usage, or provider total."""
    usage = usage or {}
    input_tokens = _non_negative_int(usage.get("input_tokens"))
    output_tokens = _non_negative_int(usage.get("output_tokens"))
    if input_tokens is not None and output_tokens is not None:
        return input_tokens + output_tokens
    total_tokens = _non_negative_int(usage.get("total_tokens"))
    return total_tokens or 0


def has_context_usage(usage: dict[str, Any] | None) -> bool:
    """Return whether a provider supplied enough data for an exact total."""
    usage = usage or {}
    if "usage_reported" in usage:
        return bool(usage.get("usage_reported"))
    input_tokens = _non_negative_int(usage.get("input_tokens"))
    output_tokens = _non_negative_int(usage.get("output_tokens"))
    return (
        input_tokens is not None and output_tokens is not None
    ) or _non_negative_int(usage.get("total_tokens")) is not None


def _first_non_negative(candidates: list[dict], keys: tuple[str, ...]) -> int | None:
    for candidate in candidates:
        for key in keys:
            value = _non_negative_int(candidate.get(key))
            if value is not None:
                return value
    return None


def _non_negative_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
