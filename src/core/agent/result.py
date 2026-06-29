"""Result aggregation for synchronous Agent turn workers."""

from uuid import uuid4

from src.core.agent.models import StopReason


ERROR_RESULT_FIELDS = (
    "error_category",
    "error_action",
    "retryable",
    "provider",
    "provider_code",
    "http_status",
    "failure_source",
    "failure_stage",
    "failure_scope",
    "user_action",
)


class TurnResultBuilder:
    """Aggregate streamed Agent events into the final RPC result dict."""

    def __init__(self, *, run_id: str | None, default_error: str) -> None:
        self.result = {"status": "error", "run_id": run_id or uuid4().hex}
        self.default_error = default_error

    @property
    def run_id(self) -> str:
        """Return the stable run id used while streaming this turn."""
        return self.result["run_id"]

    def observe(self, item: dict) -> None:
        """Update the final result from one streamed event."""
        if item["event"] == "done":
            self.result["status"] = "ok"
            self.result.update(item["data"])
            return
        if item["event"] == "paused":
            self.result["status"] = "paused"
            self.result.update(item["data"])
            return
        if item["event"] != "error":
            return
        data = item["data"]
        self.result["error"] = data.get("message", self.default_error)
        self.result["stop_reason"] = data.get(
            "stop_reason",
            StopReason.TURN_ERROR.value,
        )
        for field in ERROR_RESULT_FIELDS:
            if field in data:
                self.result[field] = data[field]

    def build(self) -> dict:
        """Return the aggregated result."""
        return self.result
