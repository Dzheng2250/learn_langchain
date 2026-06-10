import threading
import time
import unittest

from src.core.agent.service import AgentTurnService


class LockProbeService(AgentTurnService):
    def __init__(self):
        super().__init__(memory_enabled=False, graph=None)
        self.active = {}
        self.max_active = {}
        self.guard = threading.Lock()

    def _stream_locked_turn(self, session_id, user_input, run_id):
        with self.guard:
            self.active[session_id] = self.active.get(session_id, 0) + 1
            self.max_active[session_id] = max(
                self.max_active.get(session_id, 0),
                self.active[session_id],
            )
        time.sleep(0.08)
        with self.guard:
            self.active[session_id] -= 1
        yield {"event": "done", "data": {"status": "ok", "run_id": run_id}}


class AgentTurnServiceConcurrencyTest(unittest.TestCase):
    def test_same_session_turns_are_serialized(self):
        service = LockProbeService()
        threads = [
            threading.Thread(target=lambda: list(service.stream_turn("same", "hello")))
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(1, service.max_active["same"])
        service.close()

    def test_different_sessions_can_run_concurrently(self):
        service = LockProbeService()
        barrier = threading.Barrier(2)
        active_total = 0
        max_total = 0
        guard = threading.Lock()

        def worker(session_id):
            nonlocal active_total, max_total
            with service.lock_registry.get(session_id):
                barrier.wait()
                with guard:
                    active_total += 1
                    max_total = max(max_total, active_total)
                time.sleep(0.05)
                with guard:
                    active_total -= 1

        threads = [threading.Thread(target=worker, args=(session,)) for session in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(2, max_total)
        service.close()


if __name__ == "__main__":
    unittest.main()
