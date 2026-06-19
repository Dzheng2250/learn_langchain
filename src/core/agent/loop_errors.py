"""Error handling helpers for the foreground Turn execution loop."""

from collections.abc import Iterator

from src.core.agent.budget import ExecutionBudget
from src.core.agent.models import StopReason
from src.core.agent.responses import failed_turn_event
from src.core.agent.run_observer import TurnRunObserver
from src.core.errors import ErrorAction
from src.core.errors.provider_failure import ProviderFailureService
from src.core.state.types import ExecutionStatus
from src.core.workspace.models import SessionContext


class TurnLoopErrorHandler:
    """Translate Slice failures into persisted Execution state and stream events."""

    def __init__(
        self,
        *,
        execution_repository=None,
        provider_failure_service: ProviderFailureService | None = None,
        observer: TurnRunObserver | None = None,
    ) -> None:
        self.execution_repository = execution_repository
        self.provider_failure_service = provider_failure_service
        self.observer = observer or TurnRunObserver()

    def stream_slice_error(
        self,
        *,
        session: SessionContext,
        execution,
        run_id: str,
        item: dict,
        budget: ExecutionBudget,
    ) -> Iterator[dict]:
        """Persist a Slice error and emit the user-visible failure event."""
        if execution is not None and self.execution_repository is not None:
            usage = budget.snapshot()
            if item["data"].get("error_action") == ErrorAction.TERMINATE:
                if self.provider_failure_service is not None:
                    self.provider_failure_service.terminate_execution_after_error(
                        session,
                        execution,
                        item["data"].get(
                            "error_category",
                            StopReason.TURN_ERROR.value,
                        ),
                    )
                    yield from self.provider_failure_service.emit_terminal_provider_error(
                        session,
                        execution,
                        run_id,
                        item,
                    )
                else:
                    yield item
                return
            self.execution_repository.pause(
                execution.execution_id,
                ExecutionStatus.PAUSED_ERROR,
                item["data"].get(
                    "stop_reason",
                    StopReason.TURN_ERROR.value,
                ),
                item["data"].get("message", ""),
                usage=usage,
            )
        self.observer.run_error_item(item)
        yield item

    def stream_unexpected_exception(
        self,
        *,
        run_id: str,
        execution,
        active_slice_id: str | None,
        budget: ExecutionBudget | None,
        exc: Exception,
    ) -> Iterator[dict]:
        """Persist a generic loop failure without letting cleanup hide the error."""
        if execution is not None and self.execution_repository is not None:
            try:
                usage = budget.snapshot() if budget is not None else None
                if active_slice_id is not None:
                    self.execution_repository.finish_slice(
                        active_slice_id,
                        execution.execution_id,
                        status=ExecutionStatus.PAUSED_ERROR,
                        stop_reason=StopReason.TURN_ERROR.value,
                        usage=usage,
                    )
                self.execution_repository.pause(
                    execution.execution_id,
                    ExecutionStatus.PAUSED_ERROR,
                    StopReason.TURN_ERROR.value,
                    str(exc),
                    usage=usage,
                )
            except Exception:
                pass
        self.observer.unexpected_exception(exc)
        yield failed_turn_event(run_id, str(exc))

