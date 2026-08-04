import unittest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.core.context.manager import AgentContextManager
from src.core.context.models import AgentContextState, TurnChunk
from src.core.context.summary_executor import ContextSummaryExecutor
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
        self.requests = []

    def create_chat_model(self, *_args, **kwargs):
        self.requests.append(kwargs)
        return self.model


class AgentContextManagerTest(unittest.TestCase):
    def test_large_source_that_fits_token_budget_uses_one_request(self):
        provider = RecordingSummaryProvider()
        manager = AgentContextManager(provider)
        source = [
            HumanMessage(content="SOURCE-ALPHA" + "x" * 22_000),
            AIMessage(content="SOURCE-BETA" + "y" * 22_000),
            HumanMessage(content="SOURCE-GAMMA" + "z" * 22_000),
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
        self.assertEqual(1, len(provider.model.requests))
        self.assertEqual("summary-1", summary)
        self.assertEqual(10, input_tokens)
        self.assertEqual(2, output_tokens)

    def test_summary_source_keeps_complete_long_message_content(self):
        provider = RecordingSummaryProvider()
        executor = ContextSummaryExecutor(model_provider=provider)
        tail_marker = "SOURCE-CONTENT-TAIL"
        source = HumanMessage(content="x" * 5_000 + tail_marker)

        executor.summarize("", [source])

        rendered_request = "\n".join(
            str(message.content)
            for message in provider.model.requests[0]
        )
        self.assertIn(tail_marker, rendered_request)
        self.assertNotIn("message truncated", rendered_request)

    def test_message_volume_does_not_trigger_character_compression(self):
        policy = SummaryPolicy(turn_limit=20, token_limit=90_000)

        self.assertFalse(
            policy.should_summarize_state(
                context_tokens=1,
                turns=[TurnChunk(1, [HumanMessage(content="x" * 100_000)])],
                messages=[HumanMessage(content="x" * 100_000)],
            )
        )

    def test_oversized_source_uses_token_aware_map_reduce(self):
        provider = RecordingSummaryProvider()
        executor = ContextSummaryExecutor(
            model_provider=provider,
            model_context_limit=2_000,
            safety_margin_tokens=100,
            summary_max_tokens=400,
            map_max_tokens=100,
            map_workers=2,
        )
        source = [
            HumanMessage(content=f"SOURCE-{index}-" + (str(index) * 2_000), id=f"m-{index}")
            for index in range(3)
        ]

        summary, input_tokens, output_tokens = executor.summarize(
            "previous",
            source,
            source_groups=[[message] for message in source],
        )

        rendered = "\n".join(
            str(message.content)
            for request in provider.model.requests
            for message in request
        )
        for index in range(3):
            self.assertIn(f"SOURCE-{index}", rendered)
        system_prompts = [request[0].content for request in provider.model.requests]
        self.assertEqual(1, len(set(system_prompts)))
        self.assertNotIn("previous", system_prompts[0])
        self.assertGreater(len(provider.model.requests), 1)
        self.assertEqual(f"summary-{len(provider.model.requests)}", summary)
        self.assertEqual(10 * len(provider.model.requests), input_tokens)
        self.assertEqual(2 * len(provider.model.requests), output_tokens)
        self.assertIn(100, {item["max_tokens"] for item in provider.requests})
        self.assertIn(400, {item["max_tokens"] for item in provider.requests})

    def test_summary_output_is_not_truncated_by_character_count(self):
        provider = RecordingSummaryProvider()
        provider.model.invoke = lambda _messages: AIMessage(
            content="Z" * 20_000,
            usage_metadata={"input_tokens": 10, "output_tokens": 100, "total_tokens": 110},
        )
        executor = ContextSummaryExecutor(model_provider=provider)

        summary, _, _ = executor.summarize("", [HumanMessage(content="source")])

        self.assertEqual("Z" * 20_000, summary)

    def test_output_limit_response_rejects_the_summary(self):
        provider = RecordingSummaryProvider()
        provider.model.invoke = lambda _messages: AIMessage(
            content="partial",
            response_metadata={"stop_reason": "max_tokens"},
            usage_metadata={"input_tokens": 10, "output_tokens": 100, "total_tokens": 110},
        )
        executor = ContextSummaryExecutor(model_provider=provider)

        with self.assertRaisesRegex(
            RuntimeError,
            "LEARN_AGENT_CONTEXT_SUMMARY_MAX_TOKENS",
        ):
            executor.summarize("", [HumanMessage(content="source")])

    def test_map_output_limit_error_names_the_map_budget(self):
        provider = RecordingSummaryProvider()
        provider.model.invoke = lambda _messages: AIMessage(
            content="partial",
            response_metadata={"stop_reason": "max_tokens"},
        )
        executor = ContextSummaryExecutor(
            model_provider=provider,
            model_context_limit=2_000,
            safety_margin_tokens=100,
            summary_max_tokens=400,
            map_max_tokens=100,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "LEARN_AGENT_CONTEXT_SUMMARY_MAP_MAX_TOKENS",
        ):
            executor.summarize("", [HumanMessage(content="x" * 5_000)])

    def test_disabled_fixed_token_limit_does_not_trigger_summary(self):
        policy = SummaryPolicy(
            turn_limit=20,
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
