"""Bounded worker executor for synchronous Agent turn code."""

import asyncio
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from contextvars import copy_context
from functools import partial
from typing import TypeVar

from src.config.settings import CORE_AGENT_WORKERS


T = TypeVar("T")


class TurnWorkerExecutor:
    """Run blocking Agent work on a bounded dedicated executor."""

    def __init__(
        self,
        *,
        executor: Executor | None = None,
        max_workers: int = CORE_AGENT_WORKERS,
    ) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be greater than zero")
        self._slots = asyncio.Semaphore(max_workers)
        self._executor = executor or ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="agent-turn",
        )
        self._owns_executor = executor is None

    async def run(self, func: Callable[..., T], *args, **kwargs) -> T:
        """Submit ``func`` with current contextvars and await its result."""
        loop = asyncio.get_running_loop()
        await self._slots.acquire()
        try:
            worker_context = copy_context()
            worker_future = self._executor.submit(
                worker_context.run,
                partial(func, *args, **kwargs),
            )
        except Exception:
            self._slots.release()
            raise

        def release_slot(_future) -> None:
            try:
                loop.call_soon_threadsafe(self._slots.release)
            except RuntimeError:
                # The process event loop is already closed; no later turn can use the slot.
                pass

        worker_future.add_done_callback(release_slot)
        return await asyncio.wrap_future(worker_future)

    def close(self) -> None:
        """Close the owned executor while leaving injected executors alone."""
        if self._owns_executor:
            self._executor.shutdown(wait=True, cancel_futures=False)
