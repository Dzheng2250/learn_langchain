"""Token-aware planning for bounded conversation context windows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from math import ceil

from langchain_core.messages import messages_to_dict

from src.config.settings import (
    CONTEXT_SUMMARY_MAX_TOKENS,
    CONTEXT_SAFETY_MARGIN_TOKENS,
    CONTEXT_SOFT_LIMIT_RATIO,
    LLM_MAX_TOKENS,
    MODEL_CONTEXT_LIMIT,
    RECENT_TURN_BUDGET_RATIO,
    RECENT_TURN_LIMIT,
    SUMMARY_TRIGGER_TOKEN_LIMIT_ENABLED,
    SUMMARY_TRIGGER_TOKEN_LIMIT,
)
from src.core.context.models import TurnChunk


@dataclass(frozen=True)
class TokenCount:
    tokens: int
    estimated: bool = True


class ModelTokenCounter:
    """Conservatively estimate LangChain message and schema token volume."""

    def count_messages(self, messages: list) -> TokenCount:
        if not messages:
            return TokenCount(0)
        payload = messages_to_dict(messages)
        return self.count_value(payload)

    def count_value(self, value) -> TokenCount:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
        # UTF-8 bytes / 3 is deliberately conservative for mixed Chinese,
        # English, JSON, and tool payloads. Add fixed framing overhead.
        return TokenCount(max(1, ceil(len(encoded) / 3) + 8))


@dataclass(frozen=True)
class ContextBudget:
    model_context_limit: int
    output_reserve: int
    safety_margin: int
    hard_input_limit: int
    soft_input_limit: int
    raw_turn_limit: int
    fixed_tokens: int
    summary_reserve_tokens: int


@dataclass(frozen=True)
class ContextWindowPlan:
    compacted_turns: tuple[TurnChunk, ...]
    retained_turns: tuple[TurnChunk, ...]
    budget: ContextBudget
    source_tokens: int
    retained_tokens: int
    projected_input_tokens: int
    planned_input_tokens: int
    estimated: bool = True

    @property
    def requires_compaction(self) -> bool:
        return bool(self.compacted_turns)

    @property
    def hard_limit_exceeded(self) -> bool:
        return self.projected_input_tokens > self.budget.hard_input_limit

    @property
    def planned_hard_limit_exceeded(self) -> bool:
        return self.planned_input_tokens > self.budget.hard_input_limit


class ContextWindowPlanner:
    """Select a complete-Turn suffix that satisfies count and token limits."""

    def __init__(
        self,
        counter: ModelTokenCounter | None = None,
        *,
        model_context_limit: int = MODEL_CONTEXT_LIMIT,
        output_reserve: int = LLM_MAX_TOKENS,
        safety_margin: int = CONTEXT_SAFETY_MARGIN_TOKENS,
        soft_limit_ratio: float = CONTEXT_SOFT_LIMIT_RATIO,
        recent_turn_limit: int = RECENT_TURN_LIMIT,
        recent_turn_budget_ratio: float = RECENT_TURN_BUDGET_RATIO,
        summary_trigger_token_limit_enabled: bool = SUMMARY_TRIGGER_TOKEN_LIMIT_ENABLED,
        summary_trigger_token_limit: int = SUMMARY_TRIGGER_TOKEN_LIMIT,
        summary_max_tokens: int = CONTEXT_SUMMARY_MAX_TOKENS,
    ) -> None:
        if model_context_limit <= output_reserve + safety_margin:
            raise ValueError(
                "LLM output reserve and context safety margin exhaust MODEL_CONTEXT_LIMIT"
            )
        if recent_turn_limit < 0:
            raise ValueError("recent_turn_limit must not be negative")
        if not 0 < recent_turn_budget_ratio <= 0.5:
            raise ValueError("recent_turn_budget_ratio must be in (0, 0.5]")
        if not 0 < soft_limit_ratio <= 1:
            raise ValueError("soft_limit_ratio must be in (0, 1]")
        self.counter = counter or ModelTokenCounter()
        self.model_context_limit = int(model_context_limit)
        self.output_reserve = int(output_reserve)
        self.safety_margin = int(safety_margin)
        self.soft_limit_ratio = float(soft_limit_ratio)
        self.recent_turn_limit = int(recent_turn_limit)
        self.recent_turn_budget_ratio = float(recent_turn_budget_ratio)
        self.summary_trigger_token_limit_enabled = bool(
            summary_trigger_token_limit_enabled
        )
        self.summary_trigger_token_limit = int(summary_trigger_token_limit)
        self.summary_reserve_tokens = int(summary_max_tokens)

    def plan(
        self,
        turns: list[TurnChunk],
        *,
        fixed_messages: list | None = None,
        summary: str = "",
    ) -> ContextWindowPlan:
        fixed_count = self.counter.count_messages(fixed_messages or [])
        summary_count = self.counter.count_value(summary) if summary else TokenCount(0)
        fixed_tokens = fixed_count.tokens
        current_summary_tokens = summary_count.tokens
        hard = self.model_context_limit - self.output_reserve - self.safety_margin
        dynamic_soft = max(1, int(hard * self.soft_limit_ratio))
        soft = (
            min(self.summary_trigger_token_limit, dynamic_soft)
            if self.summary_trigger_token_limit_enabled
            else dynamic_soft
        )
        turn_counts = [self.counter.count_messages(turn.messages) for turn in turns]
        current_projected = (
            fixed_tokens
            + current_summary_tokens
            + sum(count.tokens for count in turn_counts)
        )
        token_limit_exceeded = current_projected > soft
        # Turn count and the raw-tail ratio are retention policies, not
        # compaction triggers. Below the token threshold every complete Turn
        # remains active, regardless of RECENT_TURN_LIMIT.
        planned_total_limit = soft if token_limit_exceeded else hard
        raw_limit = max(
            0,
            min(
                int(self.model_context_limit * self.recent_turn_budget_ratio),
                planned_total_limit - fixed_tokens - self.summary_reserve_tokens,
            ),
        )
        budget = ContextBudget(
            self.model_context_limit,
            self.output_reserve,
            self.safety_margin,
            hard,
            soft,
            raw_limit,
            fixed_tokens,
            self.summary_reserve_tokens,
        )
        turn_tokens = [count.tokens for count in turn_counts]
        if not token_limit_exceeded:
            retained_tokens = sum(turn_tokens)
            return ContextWindowPlan(
                compacted_turns=(),
                retained_turns=tuple(turns),
                budget=budget,
                source_tokens=0,
                retained_tokens=retained_tokens,
                projected_input_tokens=current_projected,
                planned_input_tokens=current_projected,
                estimated=(
                    fixed_count.estimated
                    or summary_count.estimated
                    or any(count.estimated for count in turn_counts)
                ),
            )
        retained_count = 0
        retained_tokens = 0
        for token_count in reversed(turn_tokens):
            if retained_count >= self.recent_turn_limit:
                break
            if retained_tokens + token_count > raw_limit:
                break
            retained_count += 1
            retained_tokens += token_count
        split = len(turns) - retained_count
        source_tokens = sum(turn_tokens[:split])
        planned = fixed_tokens + self.summary_reserve_tokens + retained_tokens
        return ContextWindowPlan(
            compacted_turns=tuple(turns[:split]),
            retained_turns=tuple(turns[split:]),
            budget=budget,
            source_tokens=source_tokens,
            retained_tokens=retained_tokens,
            projected_input_tokens=current_projected,
            planned_input_tokens=planned,
            estimated=(
                fixed_count.estimated
                or summary_count.estimated
                or any(count.estimated for count in turn_counts)
            ),
        )
