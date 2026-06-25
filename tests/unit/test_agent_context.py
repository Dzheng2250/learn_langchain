import unittest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.core.context.manager import AgentContextManager
from src.core.context.models import AgentContextState, TurnChunk
from tests.support.model_providers import UnusedModelProvider


class AgentContextManagerTest(unittest.TestCase):
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

    def test_recent_limit_keeps_complete_turns_with_tool_messages(self):
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

        self.assertEqual([2, 3], [turn.turn_index for turn in result.recent_turns])
        self.assertEqual(
            ["middle user", "middle answer", "new user", "new answer"],
            [message.content for message in result.recent_messages],
        )


if __name__ == "__main__":
    unittest.main()
