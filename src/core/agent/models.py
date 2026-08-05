"""Immutable identity and control models for one Agent run."""

from dataclasses import dataclass
from enum import StrEnum

from src.config.settings import (
    HARD_MAX_TOOL_CALLS_PER_GRANT,
    MAX_GRAPH_STEPS_PER_SLICE,
    SUBAGENT_MAX_STEPS,
)
from src.core.workspace.models import SessionContext


class StopReason(StrEnum):
    """Stable machine-readable reasons for ending an Agent request."""
    COMPLETED = "completed"
    LLM_NOT_CONFIGURED = "llm_not_configured"
    GRAPH_STEP_LIMIT = "graph_step_limit"
    TOOL_CALL_LIMIT = "tool_call_limit"
    BUDGET_LIMIT = "budget_limit"
    GRANT_WALL_TIME_LIMIT = "grant_wall_time_limit"
    CLIENT_DISCONNECTED = "client_disconnected"
    TOOL_APPROVAL = "tool_approval"
    TOOL_RECOVERY_REQUIRED = "tool_recovery_required"
    MODEL_OUTPUT_LIMIT = "model_output_limit"
    CONTEXT_COMPACTION_REQUIRED = "context_compaction_required"
    GRAPH_ERROR = "graph_error"
    TURN_ERROR = "turn_error"


class ResumePolicy(StrEnum):
    """Action required before a persisted execution may continue."""

    CONTINUE = "continue"
    ACTION_REQUIRED = "action_required"
    CONDITION_REQUIRED = "condition_required"
    TERMINAL = "terminal"


def resume_policy_for(stop_reason: str) -> ResumePolicy:
    """Map stable stop reasons to one recovery contract."""
    if stop_reason in {
        StopReason.TOOL_APPROVAL.value,
        StopReason.TOOL_RECOVERY_REQUIRED.value,
    }:
        return ResumePolicy.ACTION_REQUIRED
    if stop_reason in {
        StopReason.MODEL_OUTPUT_LIMIT.value,
        StopReason.CONTEXT_COMPACTION_REQUIRED.value,
        StopReason.GRAPH_ERROR.value,
        StopReason.TURN_ERROR.value,
    }:
        return ResumePolicy.CONDITION_REQUIRED
    if stop_reason in {
        StopReason.LLM_NOT_CONFIGURED.value,
        StopReason.TOOL_CALL_LIMIT.value,
    }:
        return ResumePolicy.TERMINAL
    return ResumePolicy.CONTINUE


@dataclass(frozen=True)
class RunLimits:
    """Immutable limits enforced by graph and streaming adapters."""
    max_graph_steps: int = MAX_GRAPH_STEPS_PER_SLICE
    # This is now a high emergency ceiling. Normal policy uses risk-class
    # budgets instead of treating all tools as equally expensive.
    max_tool_calls: int = HARD_MAX_TOOL_CALLS_PER_GRANT
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
    """Canonical identity and limits for one real or diagnostic Turn."""
    run_id: str
    session: SessionContext
    turn_index: int
    limits: RunLimits

    @property
    def workspace(self):
        """Expose the Session-owned Workspace without duplicating identity."""
        return self.session.workspace
