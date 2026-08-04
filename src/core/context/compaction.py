"""Coordinate token-aware context compaction before model input is built."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import HumanMessage

from src.config.settings import MODEL
from src.core.context.models import AgentContextState
from src.core.telemetry import emit_event, record_error


class ContextCompactionRequired(RuntimeError):
    """The next model call cannot fit safely until compaction succeeds."""


@dataclass(frozen=True)
class CompactionOutcome:
    state: AgentContextState
    compacted: bool
    cas_applied: bool
    plan: object


class ContextCompactionService:
    """Advance immutable context windows without dropping unsummarized Turns."""

    def __init__(self, context_manager, session_store, summary_store) -> None:
        self.context_manager = context_manager
        self.session_store = session_store
        self.summary_store = summary_store

    def ensure_for_prompt(
        self,
        session,
        state: AgentContextState,
        *,
        user_input: str,
        extra_system_messages: list | None = None,
    ) -> AgentContextState:
        if not state.context_window_id:
            target_turn = max(
                (turn.turn_index for turn in state.recent_turns),
                default=state.summary_through_turn,
            )
            source = self.summary_store.load_summary_source(session, target_turn)
            state = AgentContextState(
                summary=source.summary,
                recent_turns=list(source.turns),
                context_tokens=state.context_tokens,
                context_window_id=source.window_id,
                summary_through_turn=source.summary_through_turn,
            )
        fixed_messages = [*(extra_system_messages or []), HumanMessage(content=user_input)]
        plan = self.context_manager.plan_window(
            state,
            fixed_messages=fixed_messages,
        )
        if not plan.requires_compaction:
            if plan.hard_limit_exceeded:
                raise ContextCompactionRequired(
                    "The next model input exceeds its hard limit and contains no "
                    "older complete Turn that can be compacted safely."
                )
            return state
        try:
            outcome = self._apply_plan(session, state, plan)
        except Exception as exc:
            record_error(
                "context_compaction",
                "foreground_compaction",
                exc,
                "Foreground context compaction failed; source Turns remain active.",
                {
                    "hard_limit_exceeded": plan.hard_limit_exceeded,
                    "source_turn_count": len(plan.compacted_turns),
                    "projected_input_tokens": plan.projected_input_tokens,
                    "hard_input_limit": plan.budget.hard_input_limit,
                },
                event_type="context_compaction_failed",
            )
            if plan.hard_limit_exceeded:
                raise ContextCompactionRequired(
                    "Context reached its hard input limit and could not be compacted. "
                    "Resume after the summary provider recovers."
                ) from exc
            return state
        if not outcome.cas_applied:
            # A newer background window won the race. Reload its authoritative
            # summary and tail instead of applying stale planning output.
            refreshed = self.session_store.load_context(session)[0]
        else:
            refreshed = outcome.state
        refreshed_plan = self.context_manager.plan_window(
            refreshed,
            fixed_messages=fixed_messages,
        )
        if refreshed_plan.requires_compaction:
            # A concurrent commit may have extended the tail after our CAS.
            # Re-enter once through the authoritative state instead of sending
            # an input that no longer matches the completed plan.
            return self.ensure_for_prompt(
                session,
                refreshed,
                user_input=user_input,
                extra_system_messages=extra_system_messages,
            )
        if refreshed_plan.hard_limit_exceeded or refreshed_plan.planned_hard_limit_exceeded:
            raise ContextCompactionRequired(
                "Context compaction completed, but fixed prompt content still exceeds "
                "the model input hard limit."
            )
        return refreshed

    def compact_committed(self, session, target_turn: int) -> CompactionOutcome:
        source = self.summary_store.load_summary_source(session, target_turn)
        state = AgentContextState(
            summary=source.summary,
            recent_turns=list(source.turns),
            context_window_id=source.window_id,
            summary_through_turn=source.summary_through_turn,
        )
        plan = self.context_manager.plan_window(state)
        if not plan.requires_compaction:
            return CompactionOutcome(state, False, False, plan)
        return self._apply_plan(session, state, plan)

    def _apply_plan(self, session, state, plan) -> CompactionOutcome:
        messages = [
            message
            for turn in plan.compacted_turns
            for message in turn.messages
        ]
        summary, input_tokens, output_tokens = (
            self.context_manager.summarize_messages_with_usage(
                state.summary,
                messages,
            )
        )
        through_turn = plan.compacted_turns[-1].turn_index
        updated = self.summary_store.update_summary_cas(
            session,
            expected_window_id=state.context_window_id,
            summary_through_turn=through_turn,
            summary=summary,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=MODEL,
        )
        emit_event(
            "context_compaction_completed" if updated else "context_compaction_stale",
            "context_compaction",
            "Context window compaction finished.",
            {
                "updated": updated,
                "summary_through_turn": through_turn,
                "compacted_turn_count": len(plan.compacted_turns),
                "retained_turn_count": len(plan.retained_turns),
                "retained_tokens": plan.retained_tokens,
                "raw_turn_limit": plan.budget.raw_turn_limit,
            },
        )
        if not updated:
            return CompactionOutcome(state, True, False, plan)
        refreshed = self.session_store.load_context(session)[0]
        return CompactionOutcome(refreshed, True, True, plan)
