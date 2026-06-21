"""Durable LangGraph checkpoint lifecycle port."""

from typing import Protocol


class CheckpointStore(Protocol):
    """Initialize, inspect, delete, and close resumable graph checkpoints."""

    def initialize(self): ...

    def delete_thread(self, thread_id: str) -> None: ...

    def thread_exists(self, thread_id: str) -> bool: ...

    def close(self) -> None: ...
