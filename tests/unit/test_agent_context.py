import unittest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.core.context.manager import AgentContextManager
from src.core.context.models import AgentContextState
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
        )

        self.assertEqual(["question", "answer"], [message.content for message in result.recent_messages])

    def test_extract_turn_messages_ignores_synthetic_context_across_resume(self):
        manager = AgentContextManager(UnusedModelProvider(), recent_message_limit=2)
        old = [HumanMessage(content="old user"), AIMessage(content="old answer")]
        state = AgentContextState(summary="summary", recent_messages=old)
        final = [
            SystemMessage(content="Conversation context summary:\nsummary"),
            SystemMessage(content="Relevant long-term memory for this workspace:\nfact"),
            *old,
            HumanMessage(content="new request"),
            AIMessage(content="new answer"),
        ]

        extracted = manager.extract_turn_messages(state, final)

        self.assertEqual(["new request", "new answer"], [message.content for message in extracted])


if __name__ == "__main__":
    unittest.main()
