import asyncio
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from src.core.agent.worker import TurnWorkerExecutor


class TurnWorkerExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def test_bounds_concurrent_submitted_work(self):
        executor = ThreadPoolExecutor(max_workers=2)
        worker = TurnWorkerExecutor(executor=executor, max_workers=2)
        active = 0
        max_active = 0
        guard = threading.Lock()

        def run_sync():
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.04)
            with guard:
                active -= 1
            return "ok"

        try:
            results = await asyncio.gather(*(worker.run(run_sync) for _ in range(5)))
        finally:
            worker.close()
            executor.shutdown()

        self.assertEqual(["ok"] * 5, results)
        self.assertEqual(2, max_active)

    async def test_cancelled_waiter_keeps_slot_until_worker_finishes(self):
        executor = ThreadPoolExecutor(max_workers=2)
        worker = TurnWorkerExecutor(executor=executor, max_workers=1)
        first_started = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        calls = 0

        def run_sync():
            nonlocal calls
            calls += 1
            if calls == 1:
                first_started.set()
                release_first.wait(timeout=1)
            else:
                second_started.set()
            return "ok"

        first = asyncio.create_task(worker.run(run_sync))
        self.assertTrue(await asyncio.to_thread(first_started.wait, 1))
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first

        second = asyncio.create_task(worker.run(run_sync))
        await asyncio.sleep(0.02)
        self.assertFalse(second_started.is_set())
        release_first.set()
        self.assertEqual("ok", await second)

        worker.close()
        executor.shutdown()

    async def test_does_not_close_injected_executor(self):
        executor = ThreadPoolExecutor(max_workers=1)
        worker = TurnWorkerExecutor(executor=executor)

        worker.close()

        try:
            self.assertEqual("still-open", executor.submit(lambda: "still-open").result())
        finally:
            executor.shutdown()

    async def test_rejects_invalid_worker_count(self):
        with self.assertRaises(ValueError):
            TurnWorkerExecutor(max_workers=0)


if __name__ == "__main__":
    unittest.main()
