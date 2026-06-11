import threading
import time
import unittest
from uuid import uuid4

from src.core.agent.service import SessionLockRegistry


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


if __name__ == "__main__":
    unittest.main()
