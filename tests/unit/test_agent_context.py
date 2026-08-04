import unittest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.core.context.manager import AgentContextManager
from src.core.context.models import AgentContextState, TurnChunk
from src.core.context.summary_policy import SummaryPolicy
from tests.support.model_providers import UnusedModelProvider


class RecordingSummaryModel:
    def __init__(self):
        self.requests = []

    def invoke(self, messages):
        self.requests.append(messages)
        return AIMessage(
            content=f"summary-{len(self.requests)}",
            usage_metadata={
                "input_tokens": 10,
                "output_tokens": 2,
                "total_tokens": 12,
            },
        )


class RecordingSummaryProvider:
    def __init__(self):
        self.model = RecordingSummaryModel()

    def create_chat_model(self, *_args, **_kwargs):
        return self.model


class AgentContextManagerTest(unittest.TestCase):
    def test_hierarchical_summary_sends_every_source_message(self):
        provider = RecordingSummaryProvider()
        manager = AgentContextManager(
            provider,
            summary_source_char_limit=80,
        )
        source = [
            HumanMessage(content="SOURCE-ALPHA"),
            AIMessage(content="SOURCE-BETA"),
            HumanMessage(content="SOURCE-GAMMA"),
        ]

        summary, input_tokens, output_tokens = manager.summarize_messages_with_usage(
            "previous",
            source,
        )

        rendered_requests = "\n".join(
            str(message.content)
            for request in provider.model.requests
            for message in request
        )
        for marker in ("SOURCE-ALPHA", "SOURCE-BETA", "SOURCE-GAMMA"):
            self.assertIn(marker, rendered_requests)
        self.assertEqual(f"summary-{len(provider.model.requests)}", summary)
        self.assertEqual(10 * len(provider.model.requests), input_tokens)
        self.assertEqual(2 * len(provider.model.requests), output_tokens)

    def test_zero_character_limit_disables_character_trigger(self):
        policy = SummaryPolicy(turn_limit=20, char_limit=0, token_limit=90_000)

        self.assertFalse(
            policy.should_summarize_state(
                context_tokens=1,
                turns=[TurnChunk(1, [HumanMessage(content="x" * 100_000)])],
                messages=[HumanMessage(content="x" * 100_000)],
            )
        )

    def test_disabled_fixed_token_limit_does_not_trigger_summary(self):
        policy = SummaryPolicy(
            turn_limit=20,
            char_limit=0,
            token_limit=90_000,
            token_limit_enabled=False,
        )

        self.assertFalse(
            policy.should_summarize_state(
                context_tokens=100_000,
                turns=[TurnChunk(1, [HumanMessage(content="small")])],
                messages=[HumanMessage(content="small")],
            )
        )

    def test_workspace_memory_injection_is_not_saved_as_recent_history(self):
        manager = AgentContextManager(UnusedModelProvider())
        result = manager.update_after_turn(
            AgentContextState(),
            [
                SystemMessage(content="Relevant long-term memory for this workspace:\n- durable fact"),
                HumanMessage(content="question"),
                AIMessage(content="answer"),
            ],
            turn_index=1,
        )

        self.assertEqual(["question", "answer"], [message.content for message in result.recent_messages])

    def test_legacy_memory_injection_is_also_removed(self):
        manager = AgentContextManager(UnusedModelProvider())
        result = manager.update_after_turn(
            AgentContextState(),
            [
                SystemMessage(content="Relevant long-term memory:\n- durable fact"),
                HumanMessage(content="question"),
                AIMessage(content="answer"),
            ],
            turn_index=1,
        )

        self.assertEqual(["question", "answer"], [message.content for message in result.recent_messages])

    def test_extract_turn_messages_ignores_synthetic_context_across_resume(self):
        manager = AgentContextManager(UnusedModelProvider(), recent_turn_limit=2)
        old = [HumanMessage(content="old user"), AIMessage(content="old answer")]
        state = AgentContextState(summary="summary", recent_turns=[TurnChunk(1, old)])
        final = [
            SystemMessage(content="Conversation context summary:\nsummary"),
            SystemMessage(content="Relevant long-term memory for this workspace:\nfact"),
            *old,
            HumanMessage(content="new request"),
            AIMessage(content="new answer"),
        ]

        extracted = manager.extract_turn_messages(state, final, turn_index=2)

        self.assertEqual(["new request", "new answer"], [message.content for message in extracted])

    def test_extract_turn_messages_skips_existing_current_turn_on_resume(self):
        manager = AgentContextManager(UnusedModelProvider(), recent_turn_limit=2)
        current = [HumanMessage(content="new request")]
        state = AgentContextState(recent_turns=[TurnChunk(2, current)])
        final = [*current, AIMessage(content="new answer")]

        extracted = manager.extract_turn_messages(state, final, turn_index=2)

        self.assertEqual(["new answer"], [message.content for message in extracted])

    def test_fast_state_does_not_evict_turns_before_compaction_succeeds(self):
        manager = AgentContextManager(UnusedModelProvider(), recent_turn_limit=2)
        state = AgentContextState(
            recent_turns=[
                TurnChunk(1, [HumanMessage(content="old user"), AIMessage(content="old answer")]),
                TurnChunk(2, [HumanMessage(content="middle user"), AIMessage(content="middle answer")]),
            ]
        )

        result = manager.build_fast_state(
            state,
            [HumanMessage(content="new user"), AIMessage(content="new answer")],
            turn_index=3,
        )

        self.assertEqual([1, 2, 3], [turn.turn_index for turn in result.recent_turns])
        self.assertEqual(
            [
                "old user",
                "old answer",
                "middle user",
                "middle answer",
                "new user",
                "new answer",
            ],
            [message.content for message in result.recent_messages],
        )


if __name__ == "__main__":
    unittest.main()
