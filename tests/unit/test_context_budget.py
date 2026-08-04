import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.core.agent.context_guard import InTurnContextGuard, _closed_tool_cycles
from src.core.context.budget import ContextWindowPlanner, TokenCount
from src.core.context.compaction import (
    ContextCompactionRequired,
    ContextCompactionService,
)
from src.core.context.models import AgentContextState, TurnChunk


class ContentTokenCounter:
    """Use integer message content as deterministic token counts."""

    def count_messages(self, messages):
        return TokenCount(
            sum(int(getattr(message, "content", 0) or 0) for message in messages),
            estimated=False,
        )

    def count_value(self, _value):
        return TokenCount(0, estimated=False)


def turn(index, tokens):
    return TurnChunk(index, [HumanMessage(content=str(tokens))])


class ContextWindowPlannerTest(unittest.TestCase):
    def planner(self):
        return ContextWindowPlanner(
            ContentTokenCounter(),
            model_context_limit=1000,
            output_reserve=100,
            safety_margin=100,
            soft_limit_ratio=0.85,
            recent_turn_limit=3,
            recent_turn_budget_ratio=0.5,
            summary_trigger_token_limit=900,
            summary_max_chars=0,
        )

    def test_five_small_turns_keep_latest_three(self):
        plan = self.planner().plan([turn(index, 50) for index in range(1, 6)])

        self.assertEqual([1, 2], [item.turn_index for item in plan.compacted_turns])
        self.assertEqual([3, 4, 5], [item.turn_index for item in plan.retained_turns])

    def test_token_budget_reduces_suffix_from_three_to_zero(self):
        cases = [
            ([150, 150, 150], 3),
            ([200, 200, 200], 2),
            ([100, 300, 300], 1),
            ([100, 100, 600], 0),
        ]

        for tokens, retained in cases:
            with self.subTest(tokens=tokens):
                plan = self.planner().plan(
                    [turn(index, value) for index, value in enumerate(tokens, 1)]
                )
                self.assertEqual(retained, len(plan.retained_turns))
                self.assertLessEqual(plan.retained_tokens, plan.budget.raw_turn_limit)

    def test_fixed_input_without_compactable_turn_can_exceed_hard_limit(self):
        plan = self.planner().plan([], fixed_messages=[HumanMessage(content="900")])

        self.assertFalse(plan.requires_compaction)
        self.assertTrue(plan.hard_limit_exceeded)


class FakeManager:
    def __init__(self, planner, *, fail=False):
        self.planner = planner
        self.fail = fail

    def plan_window(self, state, *, fixed_messages=None):
        return self.planner.plan(
            state.recent_turns,
            fixed_messages=fixed_messages,
            summary=state.summary,
        )

    def summarize_messages_with_usage(self, previous, messages):
        if self.fail:
            raise RuntimeError("summary unavailable")
        return f"{previous}|summary:{len(messages)}", 12, 3


class FakeSessionStore:
    def __init__(self, state):
        self.state = state

    def load_context(self, _session):
        return self.state, 5


class FakeSummaryStore:
    def __init__(self, refreshed, session_store=None):
        self.refreshed = refreshed
        self.session_store = session_store
        self.calls = []

    def update_summary_cas(self, _session, **kwargs):
        self.calls.append(kwargs)
        if self.session_store is not None:
            through = kwargs["summary_through_turn"]
            self.session_store.state = AgentContextState(
                summary=kwargs["summary"],
                recent_turns=[
                    item
                    for item in self.refreshed.recent_turns
                    if item.turn_index > through
                ],
                context_window_id="window-2",
                summary_through_turn=through,
            )
        return True


class ContextCompactionServiceTest(unittest.TestCase):
    def test_success_advances_expected_window_and_keeps_planned_suffix(self):
        state = AgentContextState(
            recent_turns=[turn(index, 50) for index in range(1, 5)],
            context_window_id="window-1",
        )
        planner = ContextWindowPlanner(
            ContentTokenCounter(),
            model_context_limit=1000,
            output_reserve=100,
            safety_margin=100,
            recent_turn_limit=3,
            recent_turn_budget_ratio=0.5,
            summary_max_chars=0,
        )
        session_store = FakeSessionStore(state)
        summary_store = FakeSummaryStore(state, session_store)
        service = ContextCompactionService(
            FakeManager(planner),
            session_store,
            summary_store,
        )

        result = service.ensure_for_prompt(object(), state, user_input="0")

        self.assertEqual("window-2", result.context_window_id)
        self.assertEqual([2, 3, 4], [item.turn_index for item in result.recent_turns])
        self.assertEqual("window-1", summary_store.calls[0]["expected_window_id"])
        self.assertEqual(1, summary_store.calls[0]["summary_through_turn"])

    def test_soft_failure_preserves_every_turn_and_does_not_advance_window(self):
        state = AgentContextState(
            recent_turns=[turn(index, 50) for index in range(1, 5)],
            context_window_id="window-1",
        )
        planner = ContextWindowPlanner(
            ContentTokenCounter(),
            model_context_limit=1000,
            output_reserve=100,
            safety_margin=100,
            recent_turn_limit=3,
            recent_turn_budget_ratio=0.5,
            summary_max_chars=0,
        )
        summary_store = FakeSummaryStore(state)
        service = ContextCompactionService(
            FakeManager(planner, fail=True),
            FakeSessionStore(state),
            summary_store,
        )

        result = service.ensure_for_prompt(object(), state, user_input="0")

        self.assertIs(state, result)
        self.assertEqual([], summary_store.calls)

    def test_hard_failure_pauses_without_advancing_window(self):
        state = AgentContextState(
            recent_turns=[turn(1, 60), turn(2, 60)],
            context_window_id="window-1",
        )
        planner = ContextWindowPlanner(
            ContentTokenCounter(),
            model_context_limit=100,
            output_reserve=10,
            safety_margin=10,
            soft_limit_ratio=0.8,
            recent_turn_limit=3,
            recent_turn_budget_ratio=0.5,
            summary_trigger_token_limit=100,
            summary_max_chars=0,
        )
        summary_store = FakeSummaryStore(state)
        service = ContextCompactionService(
            FakeManager(planner, fail=True),
            FakeSessionStore(state),
            summary_store,
        )

        with self.assertRaises(ContextCompactionRequired):
            service.ensure_for_prompt(object(), state, user_input="0")
        self.assertEqual([], summary_store.calls)


class FixedCounter:
    def count_messages(self, messages):
        return TokenCount(len(messages) * 10, estimated=False)

    def count_value(self, value):
        return TokenCount(len(value) * 10 if isinstance(value, list) else 0, False)


class FakeExecutor:
    def summarize(self, previous, messages):
        return f"{previous}closed:{len(messages)}", 20, 5


class FullCheckpointOvercountingCounter(FixedCounter):
    """Simulate metadata overhead when a complete checkpoint is recounted."""

    def count_messages(self, messages):
        if len(messages) > 3:
            return TokenCount(100, estimated=False)
        return super().count_messages(messages)


class InTurnContextGuardTest(unittest.TestCase):
    @staticmethod
    def journal():
        return [
            HumanMessage(content="request", id="human"),
            AIMessage(
                content="",
                id="assistant-1",
                tool_calls=[{"id": "call-1", "name": "first", "args": {}}],
            ),
            ToolMessage(content="result-1", tool_call_id="call-1", id="tool-1"),
            AIMessage(
                content="",
                id="assistant-2",
                tool_calls=[{"id": "call-2", "name": "second", "args": {}}],
            ),
            ToolMessage(content="result-2", tool_call_id="call-2", id="tool-2"),
        ]

    def test_closed_cycle_detection_ignores_incomplete_tool_call(self):
        journal = self.journal()[:-1]

        cycles = _closed_tool_cycles(journal, 1)

        self.assertEqual(1, len(cycles))
        self.assertEqual(["assistant-1", "tool-1"], [m.id for m in cycles[0][2]])

    def test_guard_compacts_oldest_closed_cycle_but_keeps_full_journal(self):
        counter = FixedCounter()
        planner = ContextWindowPlanner(
            counter,
            model_context_limit=100,
            output_reserve=10,
            safety_margin=10,
            soft_limit_ratio=0.5,
            recent_turn_limit=3,
            recent_turn_budget_ratio=0.5,
            summary_trigger_token_limit=100,
            summary_max_chars=0,
        )
        journal = self.journal()
        guard = InTurnContextGuard(
            object(),
            system_message=HumanMessage(content="system"),
            tools=[],
            counter=counter,
            planner=planner,
            executor=FakeExecutor(),
        )

        update = guard(
            {
                "messages": journal,
                "turn_journal": journal,
                "working_summary": "",
                "compacted_journal_count": 1,
                "compaction_generation": 0,
            }
        )

        self.assertEqual(
            ["assistant-1", "tool-1"],
            [message.id for message in update["messages"]],
        )
        self.assertEqual(3, update["compacted_journal_count"])
        self.assertEqual(1, update["compaction_generation"])
        self.assertEqual(5, len(journal))

    def test_guard_uses_current_turn_provider_usage_instead_of_full_state_estimate(self):
        counter = FixedCounter()
        planner = ContextWindowPlanner(
            counter,
            model_context_limit=100,
            output_reserve=10,
            safety_margin=10,
            soft_limit_ratio=0.5,
            recent_turn_limit=3,
            recent_turn_budget_ratio=0.5,
            summary_trigger_token_limit=100,
            summary_max_chars=0,
        )
        journal = self.journal()[:3]
        journal[1].usage_metadata = {"input_tokens": 10, "output_tokens": 1}
        # Extra historical messages make full-state serialization exceed the
        # hard limit even though the provider reported a small current input.
        active = [HumanMessage(content="history") for _ in range(10)] + journal
        guard = InTurnContextGuard(
            object(),
            system_message=HumanMessage(content="system"),
            tools=[],
            counter=counter,
            planner=planner,
            executor=FakeExecutor(),
        )

        update = guard(
            {
                "messages": active,
                "turn_journal": journal,
                "working_summary": "",
                "compacted_journal_count": 1,
                "compaction_generation": 0,
            }
        )

        self.assertEqual({}, update)

    def test_guard_keeps_provider_projection_after_successful_compaction(self):
        counter = FullCheckpointOvercountingCounter()
        planner = ContextWindowPlanner(
            counter,
            model_context_limit=100,
            output_reserve=10,
            safety_margin=10,
            soft_limit_ratio=0.5,
            recent_turn_limit=3,
            recent_turn_budget_ratio=0.5,
            summary_trigger_token_limit=100,
            summary_max_chars=0,
        )
        journal = self.journal()
        journal[3].usage_metadata = {"input_tokens": 45, "output_tokens": 5}
        guard = InTurnContextGuard(
            object(),
            system_message=HumanMessage(content="system"),
            tools=[],
            counter=counter,
            planner=planner,
            executor=FakeExecutor(),
        )

        update = guard(
            {
                "messages": journal,
                "turn_journal": journal,
                "working_summary": "",
                "compacted_journal_count": 1,
                "compaction_generation": 0,
            }
        )

        self.assertEqual(["assistant-1", "tool-1"], [m.id for m in update["messages"]])
        self.assertEqual(1, update["compaction_generation"])


if __name__ == "__main__":
    unittest.main()
