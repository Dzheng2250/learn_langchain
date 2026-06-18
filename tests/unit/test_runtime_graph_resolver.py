import unittest
from types import SimpleNamespace

from src.core.agent.runtime_graph import RuntimeGraphResolver


class FakeGraph:
    def __init__(self):
        self.updates = []

    def update_state(self, config, values, *, as_node):
        self.updates.append((config, values, as_node))


class FakeRuntimeRegistry:
    def __init__(self):
        self.graph = FakeGraph()
        self.goal_graph = FakeGraph()
        self.calls = []

    def get(self, workspace):
        self.calls.append(workspace)
        return SimpleNamespace(graph=self.graph, goal_graph=self.goal_graph)


class RuntimeGraphResolverTest(unittest.TestCase):
    def test_selects_regular_or_goal_graph_for_turn(self):
        registry = FakeRuntimeRegistry()
        resolver = RuntimeGraphResolver(registry)

        self.assertIs(registry.graph, resolver.graph_for_turn("workspace", goal_mode=False))
        self.assertIs(registry.goal_graph, resolver.graph_for_turn("workspace", goal_mode=True))
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

        self.assertIs(registry.goal_graph, graph)
        self.assertEqual(1, len(graph.updates))
        config, values, as_node = graph.updates[0]
        self.assertEqual({"configurable": {"thread_id": "thread-1"}}, config)
        self.assertEqual("agent", as_node)
        self.assertEqual(
            "Additional resume instruction: continue carefully",
            values["messages"][0].content,
        )

    def test_resume_skips_blank_instruction_update(self):
        registry = FakeRuntimeRegistry()
        resolver = RuntimeGraphResolver(registry)
        pending = SimpleNamespace(goal_mode=False, checkpoint_thread_id="thread-1")

        graph = resolver.graph_for_resume("workspace", pending, instruction=" ")

        self.assertIs(registry.graph, graph)
        self.assertEqual([], graph.updates)


if __name__ == "__main__":
    unittest.main()
