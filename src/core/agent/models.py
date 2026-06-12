"""Immutable identity and control models for one Agent run."""

from dataclasses import dataclass
from enum import StrEnum

from src.config.settings import MAX_GRAPH_STEPS, MAX_TOOL_CALLS_PER_TURN, SUBAGENT_MAX_STEPS
from src.core.workspace.models import SessionContext


class StopReason(StrEnum):
    COMPLETED = "completed"
    LLM_NOT_CONFIGURED = "llm_not_configured"
    GRAPH_STEP_LIMIT = "graph_step_limit"
    TOOL_CALL_LIMIT = "tool_call_limit"
    GRAPH_ERROR = "graph_error"
    TURN_ERROR = "turn_error"


@dataclass(frozen=True)
class RunLimits:
    max_graph_steps: int = MAX_GRAPH_STEPS
    max_tool_calls: int = MAX_TOOL_CALLS_PER_TURN
    max_subagent_steps: int = SUBAGENT_MAX_STEPS

    def __post_init__(self) -> None:
        if self.max_graph_steps <= 0:
            raise ValueError("max_graph_steps must be greater than zero")
        if self.max_tool_calls <= 0:
            raise ValueError("max_tool_calls must be greater than zero")
        if self.max_subagent_steps <= 0:
            raise ValueError("max_subagent_steps must be greater than zero")


@dataclass(frozen=True)
class AgentRunContext:
    run_id: str
    session: SessionContext
    turn_index: int
    limits: RunLimits

    @property
    def workspace(self):
        return self.session.workspace
