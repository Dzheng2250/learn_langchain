"""Reusable bounded background batch processor for best-effort workloads."""

import queue
import threading
import time
from collections.abc import Callable
from typing import Generic, TypeVar


ItemT = TypeVar("ItemT")


class BoundedBatchWorker(Generic[ItemT]):
    """Drain a bounded thread-safe queue into batches without blocking producers."""

    def __init__(
        self,
        handler: Callable[[list[ItemT]], None],
        *,
        batch_size: int,
        flush_interval_seconds: float,
        queue_max_size: int,
        name: str,
        on_error: Callable[[Exception], None] | None = None,
        on_drop: Callable[[ItemT], None] | None = None,
    ) -> None:
        self.handler = handler
        self.batch_size = max(1, int(batch_size))
        self.flush_interval_seconds = max(0.01, float(flush_interval_seconds))
        self.on_error = on_error
        self.on_drop = on_drop
        self._queue: queue.Queue[ItemT | None] = queue.Queue(maxsize=max(1, int(queue_max_size)))
        self._closed = False
        self._state_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def submit(self, item: ItemT) -> bool:
        """Queue one item immediately, returning false when it must be dropped."""
        with self._state_lock:
            if self._closed:
                return False
            try:
                self._queue.put_nowait(item)
                return True
            except queue.Full:
                if self.on_drop is not None:
                    self.on_drop(item)
                return False

    def flush(self) -> None:
        """Wait until all currently queued items have been handled."""
        self._queue.join()

    def close(self, timeout_seconds: float = 2.0) -> None:
        """Stop accepting items and drain the queue within the caller's timeout."""
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            # The worker will make space shortly. Waiting here is acceptable
            # only during shutdown, never on the producer path.
            try:
                self._queue.put(None, timeout=max(0.01, timeout_seconds))
            except queue.Full:
                return
        self._thread.join(timeout=max(0.01, timeout_seconds))

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return

            batch = [item]
            stop_after_batch = False
            deadline = time.monotonic() + self.flush_interval_seconds
            while len(batch) < self.batch_size:
                timeout = max(0, deadline - time.monotonic())
                if timeout == 0:
                    break
                try:
                    next_item = self._queue.get(timeout=timeout)
                except queue.Empty:
                    break
                if next_item is None:
                    self._queue.task_done()
                    stop_after_batch = True
                    break
                batch.append(next_item)

            try:
                self.handler(batch)
            except Exception as exc:
                if self.on_error is not None:
                    self.on_error(exc)
            finally:
                for _item in batch:
                    self._queue.task_done()

            if stop_after_batch:
                return
