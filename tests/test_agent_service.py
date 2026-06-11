import asyncio
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
from uuid import uuid4

from src.config.settings import CORE_AGENT_WORKERS
from src.core.agent.service import AgentTurnService, SessionLockRegistry


class SessionLockRegistryTest(unittest.TestCase):
    def test_same_internal_session_is_serialized(self):
        registry = SessionLockRegistry()
        session_id = uuid4()
        active = 0
        max_active = 0
        guard = threading.Lock()

        def worker():
            nonlocal active, max_active
            with registry.get(session_id):
                with guard:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.04)
                with guard:
                    active -= 1

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(1, max_active)

    def test_same_name_can_run_concurrently_with_different_internal_ids(self):
        registry = SessionLockRegistry()
        barrier = threading.Barrier(2)
        active = 0
        max_active = 0
        guard = threading.Lock()

        def worker(session_id):
            nonlocal active, max_active
            with registry.get(session_id):
                barrier.wait()
                with guard:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.03)
                with guard:
                    active -= 1

        threads = [threading.Thread(target=worker, args=(uuid4(),)) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(2, max_active)


class AgentTurnExecutorTest(unittest.IsolatedAsyncioTestCase):
    def _service(self, executor=None, max_concurrent_turns=CORE_AGENT_WORKERS):
        return AgentTurnService(
            workspace_repository=Mock(),
            runtime_registry=Mock(),
            memory_store_factory=Mock(),
            turn_executor=executor,
            max_concurrent_turns=max_concurrent_turns,
        )

    async def test_injected_executor_bounds_concurrent_turns(self):
        executor = ThreadPoolExecutor(max_workers=2)
        service = self._service(executor, max_concurrent_turns=2)
        active = 0
        max_active = 0
        guard = threading.Lock()

        def run_sync(*_args, **_kwargs):
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.04)
            with guard:
                active -= 1
            return {"status": "ok"}

        service._run_turn_sync = run_sync
        try:
            await asyncio.gather(*(service.run_turn(".", f"s-{index}", "hello") for index in range(5)))
        finally:
            service.close()
            executor.shutdown()

        self.assertEqual(2, max_active)

    async def test_service_does_not_close_injected_executor(self):
        executor = ThreadPoolExecutor(max_workers=1)
        service = self._service(executor)
        service.close()
        try:
            self.assertEqual("still-open", executor.submit(lambda: "still-open").result())
        finally:
            executor.shutdown()

    async def test_default_executor_uses_configured_worker_limit(self):
        service = self._service()
        try:
            self.assertEqual(CORE_AGENT_WORKERS, service._turn_executor._max_workers)
        finally:
            service.close()

    async def test_rejects_invalid_concurrency_limit(self):
        with self.assertRaises(ValueError):
            self._service(max_concurrent_turns=0)

    async def test_cancelled_waiter_keeps_slot_until_worker_finishes(self):
        executor = ThreadPoolExecutor(max_workers=2)
        service = self._service(executor, max_concurrent_turns=1)
        first_started = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        calls = 0

        def run_sync(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                first_started.set()
                release_first.wait(timeout=1)
            else:
                second_started.set()
            return {"status": "ok"}

        service._run_turn_sync = run_sync
        first = asyncio.create_task(service.run_turn(".", "first", "hello"))
        self.assertTrue(await asyncio.to_thread(first_started.wait, 1))
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first

        second = asyncio.create_task(service.run_turn(".", "second", "hello"))
        await asyncio.sleep(0.02)
        self.assertFalse(second_started.is_set())
        release_first.set()
        await second

        service.close()
        executor.shutdown()


if __name__ == "__main__":
    unittest.main()
