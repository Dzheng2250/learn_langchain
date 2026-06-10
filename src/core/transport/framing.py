"""NDJSON framing helpers for the socket transport."""

import asyncio
import json


class FrameError(ValueError):
    """Raised when one transport frame is invalid."""


async def read_ndjson(reader: asyncio.StreamReader, max_bytes: int):
    """Read and decode one bounded NDJSON value."""
    try:
        line = await reader.readline()
    except (ValueError, asyncio.LimitOverrunError) as exc:
        raise FrameError("message exceeds maximum size") from exc
    if not line:
        return None
    if len(line) > max_bytes:
        raise FrameError("message exceeds maximum size")
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FrameError("message is not valid UTF-8") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise FrameError("invalid JSON") from exc


def encode_ndjson(value) -> bytes:
    """Encode one JSON-compatible value as an NDJSON frame."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(exclude_none=True)
    return (json.dumps(value, ensure_ascii=False, default=repr) + "\n").encode("utf-8")
