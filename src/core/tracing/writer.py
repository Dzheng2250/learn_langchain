"""Daily-rotated, best-effort JSONL trace writer."""

import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.core.common.batching import BoundedBatchWorker
from src.core.common.debug import debug_print
from src.core.tracing.models import TraceRecord


class TraceWriter:
    """Append trace batches by UTC date without blocking record producers."""

    def __init__(
        self,
        root: Path,
        *,
        retention_days: int,
        batch_size: int,
        flush_interval_seconds: float,
        queue_max_size: int,
        today_provider=None,
    ) -> None:
        self.root = Path(root)
        self.retention_days = max(1, int(retention_days))
        self.today_provider = today_provider or (lambda: datetime.now(timezone.utc).date())
        self.dropped = 0
        self._worker = BoundedBatchWorker(
            self._write_batch,
            batch_size=batch_size,
            flush_interval_seconds=flush_interval_seconds,
            queue_max_size=queue_max_size,
            name="trace-writer",
            on_error=lambda exc: debug_print("TRACE WRITE ERROR", str(exc)),
            on_drop=self._on_drop,
        )

    def emit(self, record: TraceRecord) -> None:
        self._worker.submit(record)

    def flush(self) -> None:
        self._worker.flush()

    def close(self, timeout_seconds: float = 2.0) -> None:
        self._worker.close(timeout_seconds)

    def cleanup(self) -> None:
        """Remove complete date directories older than the retention window."""
        try:
            if not self.root.exists():
                return
            cutoff = self.today_provider() - timedelta(days=self.retention_days - 1)
            for child in self.root.iterdir():
                if not child.is_dir():
                    continue
                try:
                    child_date = date.fromisoformat(child.name)
                except ValueError:
                    continue
                if child_date < cutoff:
                    try:
                        shutil.rmtree(child)
                    except OSError as exc:
                        debug_print("TRACE RETENTION ERROR", str(exc))
        except OSError as exc:
            debug_print("TRACE RETENTION ERROR", str(exc))

    def _write_batch(self, records: list[TraceRecord]) -> None:
        grouped: dict[str, list[TraceRecord]] = {}
        for record in records:
            day = record.timestamp.astimezone(timezone.utc).date().isoformat()
            grouped.setdefault(day, []).append(record)
        for day, batch in grouped.items():
            path = self.root / day / "daemon.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                for record in batch:
                    stream.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def _on_drop(self, _record: TraceRecord) -> None:
        self.dropped += 1
        debug_print("TRACE QUEUE FULL", f"dropped={self.dropped}")
