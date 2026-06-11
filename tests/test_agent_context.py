import unittest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.core.context.manager import AgentContextManager
from src.core.context.models import AgentContextState


class AgentContextManagerTest(unittest.TestCase):
    def test_workspace_memory_injection_is_not_saved_as_recent_history(self):
        manager = AgentContextManager()
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
        manager = AgentContextManager()
        result = manager.update_after_turn(
            AgentContextState(),
            [
                SystemMessage(content="Relevant long-term memory:\n- durable fact"),
                HumanMessage(content="question"),
                AIMessage(content="answer"),
            ],
        )

        self.assertEqual(["question", "answer"], [message.content for message in result.recent_messages])


if __name__ == "__main__":
    unittest.main()
