import unittest
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from src.core.agent.runtime_graph import RuntimeGraphResolver


class FakeGraph:
    def __init__(self):
        self.updates = []
        self.messages = []

    def update_state(self, config, values, *, as_node):
        self.updates.append((config, values, as_node))

    def get_state(self, _config):
        return SimpleNamespace(values={"messages": self.messages})


class FakeRuntimeRegistry:
    def __init__(self):
        self.graph = FakeGraph()
        self.calls = []

    def get(self, workspace):
        self.calls.append(workspace)
        return SimpleNamespace(graph=self.graph)


class RuntimeGraphResolverTest(unittest.TestCase):
    def test_all_modes_use_the_same_stable_graph(self):
        registry = FakeRuntimeRegistry()
        resolver = RuntimeGraphResolver(registry)

        self.assertIs(registry.graph, resolver.graph_for_turn("workspace", goal_mode=False))
        self.assertIs(registry.graph, resolver.graph_for_turn("workspace", goal_mode=True))
        self.assertEqual(["workspace", "workspace"], registry.calls)

    def test_resume_injects_additional_instruction_into_pending_thread(self):
        registry = FakeRuntimeRegistry()
        resolver = RuntimeGraphResolver(registry)
        pending = SimpleNamespace(goal_mode=True, checkpoint_thread_id="thread-1")

        graph = resolver.graph_for_resume(
            "workspace",
            pending,
            instruction=" continue carefully ",
        )

        self.assertIs(registry.graph, graph)
        self.assertEqual(1, len(graph.updates))
        config, values, as_node = graph.updates[0]
        self.assertEqual({"configurable": {"thread_id": "thread-1"}}, config)
        self.assertEqual("agent", as_node)
        self.assertEqual(
            "Additional resume instruction: continue carefully",
            values["messages"][0].content,
        )

    def test_resume_repairs_nested_goal_review_checkpoint_message(self):
        from langchain_core.messages import RemoveMessage
        from langgraph.graph.message import REMOVE_ALL_MESSAGES

        registry = FakeRuntimeRegistry()
        registry.graph.messages = [
            HumanMessage(content="request"),
            {"messages": [HumanMessage(content="review")]},
        ]
        resolver = RuntimeGraphResolver(registry)
        pending = SimpleNamespace(goal_mode=True, checkpoint_thread_id="thread-1")

        resolver.graph_for_resume("workspace", pending)

        self.assertEqual(1, len(registry.graph.updates))
        _config, values, as_node = registry.graph.updates[0]
        self.assertEqual("agent", as_node)
        self.assertIsInstance(values["messages"][0], RemoveMessage)
        self.assertEqual(REMOVE_ALL_MESSAGES, values["messages"][0].id)
        self.assertEqual(
            ["request", "review"],
            [message.content for message in values["messages"][1:]],
        )

    def test_resume_rolls_back_real_checkpoint_with_nested_pending_write(self):
        builder = StateGraph(MessagesState)
        builder.add_node(
            "agent",
            lambda _state: {"messages": [AIMessage(content="answer")]},
        )
        builder.add_edge(START, "agent")
        builder.add_edge("agent", END)
        graph = builder.compile(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "broken-goal-review"}}
        graph.invoke({"messages": [HumanMessage(content="request")]}, config)

        with self.assertRaises(ValueError):
            graph.update_state(
                config,
                {"messages": {"messages": [HumanMessage(content="review")]}},
                as_node="agent",
            )

        registry = SimpleNamespace(
            get=lambda _workspace: SimpleNamespace(graph=graph)
        )
        pending = SimpleNamespace(
            goal_mode=True,
            checkpoint_thread_id="broken-goal-review",
        )

        RuntimeGraphResolver(registry).graph_for_resume("workspace", pending)

        state = graph.get_state(config)
        self.assertEqual(
            ["request", "answer"],
            [message.content for message in state.values["messages"]],
        )
        self.assertEqual((), state.next)

    def test_resume_skips_blank_instruction_update(self):
        registry = FakeRuntimeRegistry()
        resolver = RuntimeGraphResolver(registry)
        pending = SimpleNamespace(goal_mode=False, checkpoint_thread_id="thread-1")

        graph = resolver.graph_for_resume("workspace", pending, instruction=" ")

        self.assertIs(registry.graph, graph)
        self.assertEqual([], graph.updates)


if __name__ == "__main__":
    unittest.main()
