"""Per-Grant multidimensional execution budget shared by parent and sub-agent tools."""

from contextvars import ContextVar, Token
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import BoundedSemaphore, Lock
from time import monotonic

from src.config.settings import (
    HARD_MAX_TOOL_CALLS_PER_GRANT,
    MAX_CONTROLLED_EXECUTIONS_PER_GRANT,
    MAX_DELEGATIONS_PER_GRANT,
    MAX_GRANT_WALL_SECONDS,
    MAX_PARALLEL_TOOL_CALLS,
)
from src.core.tools.catalog import ToolRisk


class ToolBudgetExceeded(RuntimeError):
    """Raised before a tool executes when its Grant budget is exhausted."""


@dataclass
class ExecutionBudget:
    """Thread-safe counters for one user-authorized execution Grant."""

    max_controlled_executions: int = MAX_CONTROLLED_EXECUTIONS_PER_GRANT
    max_delegations: int = MAX_DELEGATIONS_PER_GRANT
    hard_max_tool_calls: int = HARD_MAX_TOOL_CALLS_PER_GRANT
    max_parallel_tool_calls: int = MAX_PARALLEL_TOOL_CALLS
    max_wall_seconds: float = MAX_GRANT_WALL_SECONDS
    tool_calls: int = 0
    controlled_executions: int = 0
    delegations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    _lock: Lock = field(default_factory=Lock, repr=False)
    _started_at: float = field(default_factory=monotonic, repr=False)
    _tool_slots: BoundedSemaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_parallel_tool_calls <= 0:
            raise ValueError("max_parallel_tool_calls must be greater than zero")
        if self.max_wall_seconds <= 0:
            raise ValueError("max_wall_seconds must be greater than zero")
        self._tool_slots = BoundedSemaphore(self.max_parallel_tool_calls)

    def charge(self, tool_name: str, risk: ToolRisk) -> None:
        """Atomically reserve capacity before one tool call begins."""
        with self._lock:
            if self.tool_calls >= self.hard_max_tool_calls:
                raise ToolBudgetExceeded(
                    f"Grant tool hard limit reached ({self.hard_max_tool_calls})."
                )
            if risk == ToolRisk.CONTROLLED_EXECUTION and (
                self.controlled_executions >= self.max_controlled_executions
            ):
                raise ToolBudgetExceeded(
                    f"Controlled execution budget reached ({self.max_controlled_executions})."
                )
            if risk == ToolRisk.DELEGATION and self.delegations >= self.max_delegations:
                raise ToolBudgetExceeded(f"Delegation budget reached ({self.max_delegations}).")
            self.tool_calls += 1
            if risk == ToolRisk.CONTROLLED_EXECUTION:
                self.controlled_executions += 1
            elif risk == ToolRisk.DELEGATION:
                self.delegations += 1

    @contextmanager
    def tool_slot(self):
        """Limit concurrent tool bodies without blocking unrelated Agent turns."""
        self._tool_slots.acquire()
        try:
            yield
        finally:
            self._tool_slots.release()

    def wall_time_exhausted(self) -> bool:
        """Return whether the current Grant exceeded its cooperative time budget."""
        return monotonic() - self._started_at >= self.max_wall_seconds

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "tool_calls": self.tool_calls,
                "controlled_executions": self.controlled_executions,
                "delegations": self.delegations,
                "elapsed_ms": int((monotonic() - self._started_at) * 1000),
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
            }


_current_budget: ContextVar[ExecutionBudget | None] = ContextVar("execution_budget", default=None)


def bind_execution_budget(budget: ExecutionBudget) -> Token:
    return _current_budget.set(budget)


def current_execution_budget() -> ExecutionBudget | None:
    return _current_budget.get()


def reset_execution_budget(token: Token) -> None:
    _current_budget.reset(token)
