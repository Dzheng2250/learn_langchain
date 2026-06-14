"""NDJSON framing helpers for the socket transport."""

import asyncio
import json


class FrameError(ValueError):
    """Raised when one transport frame is invalid."""

    def __init__(self, message: str, *, frame_bytes: int | None = None) -> None:
        super().__init__(message)
        self.frame_bytes = frame_bytes


async def read_ndjson(reader: asyncio.StreamReader, max_bytes: int):
    """Read and decode one bounded NDJSON value."""
    value, _frame_bytes = await read_ndjson_frame(reader, max_bytes)
    return value


async def read_ndjson_frame(reader: asyncio.StreamReader, max_bytes: int):
    """Read one bounded NDJSON value together with its exact frame size."""
    try:
        line = await reader.readline()
    except (ValueError, asyncio.LimitOverrunError) as exc:
        raise FrameError("message exceeds maximum size") from exc
    if not line:
        return None, 0
    if len(line) > max_bytes:
        raise FrameError("message exceeds maximum size", frame_bytes=len(line))
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FrameError("message is not valid UTF-8", frame_bytes=len(line)) from exc
    try:
        return json.loads(text), len(line)
    except json.JSONDecodeError as exc:
        raise FrameError("invalid JSON", frame_bytes=len(line)) from exc


def encode_ndjson(value) -> bytes:
    """Encode one JSON-compatible value as an NDJSON frame."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(exclude_none=True)
    return (json.dumps(value, ensure_ascii=False, default=repr) + "\n").encode("utf-8")
