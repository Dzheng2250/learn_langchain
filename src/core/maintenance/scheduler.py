"""Failure-isolated worker for durable maintenance jobs."""

from collections.abc import Callable
from threading import Event, Thread

from src.core.maintenance.models import MaintenanceJob
from src.core.maintenance.repository import MaintenanceRepository
from src.core.telemetry import bind_context, emit_event, record_error, reset_context


MaintenanceHandler = Callable[[MaintenanceJob], None]


class MaintenanceScheduler:
    """Poll durable jobs and dispatch them through a fixed handler registry."""

    def __init__(
        self,
        repository: MaintenanceRepository,
        handlers: dict[str, MaintenanceHandler],
        *,
        poll_interval_seconds: float = 0.25,
        lease_seconds: float = 60,
    ) -> None:
        self.repository = repository
        self.handlers = dict(handlers)
        self.poll_interval_seconds = max(0.05, float(poll_interval_seconds))
        self.lease_seconds = max(1, float(lease_seconds))
        self._stop = Event()
        self._wake = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        """Start exactly one daemon worker."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="agent-maintenance", daemon=True)
        self._thread.start()

    def wake(self) -> None:
        """Prompt the worker after a Turn enqueues new work."""
        self._wake.set()

    def close(self, timeout_seconds: float = 5) -> bool:
        """Stop claiming new work; leased jobs recover after expiry on restart."""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0, timeout_seconds))
        stopped = thread is None or not thread.is_alive()
        # Keep the reference when shutdown times out. A later close() call can
        # still join the same worker, and callers know not to close resources
        # that an in-flight handler may still be using.
        if stopped:
            self._thread = None
        return stopped

    def run_once(self) -> bool:
        """Process one ready job; return whether work was claimed."""
        job = self.repository.claim_next(lease_seconds=self.lease_seconds)
        if job is None:
            return False
        handler = self.handlers.get(job.job_type)
        if handler is None:
            self.repository.fail(job, f"No maintenance handler registered for {job.job_type}.")
            return True
        context_token = bind_context(
            workspace_id=job.workspace_id,
            session_id=job.session_id,
            turn_index=job.payload.get("turn_index") or job.payload.get("target_turn"),
        )
        try:
            handler(job)
        except Exception as exc:
            self.repository.fail(job, str(exc))
            record_error(
                "maintenance_scheduler",
                job.job_type,
                exc,
                "Background maintenance job failed.",
                {"job_id": job.job_id, "attempt": job.attempts},
                event_type="maintenance_job_failed",
            )
        else:
            self.repository.succeed(job.job_id)
            emit_event(
                "maintenance_job_succeeded",
                "maintenance_scheduler",
                "Background maintenance job completed.",
                {"job_id": job.job_id, "job_type": job.job_type},
            )
        finally:
            reset_context(context_token)
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self.run_once():
                    continue
            except Exception as exc:
                # Repository failures must not permanently kill the only
                # maintenance worker. Leased work becomes reclaimable after
                # expiry, while this loop retries after the polling interval.
                record_error(
                    "maintenance_scheduler",
                    "worker_loop",
                    exc,
                    "Maintenance worker iteration failed.",
                    event_type="maintenance_worker_failed",
                )
            self._wake.wait(self.poll_interval_seconds)
            self._wake.clear()
