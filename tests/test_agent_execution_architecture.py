import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from src.core.agent.models import AgentRunContext, RunLimits, StopReason
from src.core.context.manager import AgentContextManager
from src.core.hooks.events import (
    emit_event,
    get_event_context,
    reset_event_context,
    set_event_context,
    set_event_publisher,
    set_run_event_context,
)
from src.core.hooks.publisher import SinkEventPublisher
from src.core.llm.provider import LlmPurpose, OpenAICompatibleProvider
from src.core.memory.extractor import MemoryCandidateExtractor
from src.core.streaming.events import stream_graph_events
from src.core.tools.catalog import ToolAudience, ToolRegistry, ToolRisk, ToolSpec
from src.core.workspace.models import SessionContext, WorkspaceContext


ROOT = Path(__file__).resolve().parents[1]


class FakeModel:
    def invoke(self, _messages):
        return AIMessage(content="[]")


class RecordingProvider:
    def __init__(self):
        self.calls = []

    def create_chat_model(self, purpose, **kwargs):
        self.calls.append((purpose, kwargs))
        return FakeModel()


class MemorySink:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class FailingSink:
    def emit(self, _event):
        raise RuntimeError("sink failed")


class FakeGraph:
    def __init__(self, messages):
        self.messages = messages

    def stream(self, inputs, **_kwargs):
        yield "values", {"messages": [*inputs["messages"], *self.messages]}


class AgentExecutionArchitectureTest(unittest.TestCase):
    def tearDown(self):
        set_event_publisher(None)
        set_event_context()

    def test_model_provider_is_shared_by_non_agent_llm_workloads(self):
        provider = RecordingProvider()

        AgentContextManager(model_provider=provider)._create_summary_llm()
        MemoryCandidateExtractor(provider)._create_llm()

        self.assertEqual(
            [LlmPurpose.CONTEXT_SUMMARY, LlmPurpose.MEMORY_EXTRACTION],
            [purpose for purpose, _kwargs in provider.calls],
        )

    def test_openai_compatible_provider_owns_vendor_model_construction(self):
        bound = object()
        model = Mock()
        model.bind_tools.return_value = bound
        with patch("src.core.llm.provider.ChatOpenAI", return_value=model) as constructor:
            result = OpenAICompatibleProvider(
                model="test-model",
                api_key="key",
                base_url="https://example.test",
            ).create_chat_model(
                LlmPurpose.PARENT_AGENT,
                streaming=True,
                temperature=0.5,
                tools=["tool"],
            )

        self.assertIs(bound, result)
        constructor.assert_called_once()
        self.assertEqual(
            {"purpose": LlmPurpose.PARENT_AGENT.value},
            constructor.call_args.kwargs["metadata"],
        )
        model.bind_tools.assert_called_once_with(["tool"])

    def test_provider_reports_missing_api_key_without_network_request(self):
        status = OpenAICompatibleProvider(api_key="", base_url="https://example.test").configuration_status()

        self.assertFalse(status.configured)
        self.assertEqual(("LEARN_AGENT_LLM_API_KEY",), status.missing)

    def test_provider_reports_configured_with_generic_api_key(self):
        status = OpenAICompatibleProvider(api_key="configured").configuration_status()

        self.assertTrue(status.configured)
        self.assertEqual((), status.missing)

    def test_tool_registry_derives_audience_specific_views(self):
        registry = ToolRegistry()

        @tool
        def parent_only() -> str:
            """Parent-only tool."""
            return "ok"

        registry.register(
            ToolSpec(
                name=parent_only.name,
                tool=parent_only,
                audiences=frozenset({ToolAudience.PARENT}),
                risk=ToolRisk.READ_ONLY,
            )
        )

        self.assertEqual([parent_only], registry.tools_for(ToolAudience.PARENT))
        self.assertEqual([], registry.tools_for(ToolAudience.SUBAGENT))
        with self.assertRaises(ValueError):
            registry.register(registry.specs()[0])
        registry.freeze()
        with self.assertRaises(RuntimeError):
            registry.register(
                ToolSpec(
                    name="late",
                    tool=parent_only,
                    audiences=frozenset({ToolAudience.PARENT}),
                    risk=ToolRisk.READ_ONLY,
                )
            )

    def test_run_context_sets_complete_event_identity(self):
        sink = MemorySink()
        set_event_publisher(SinkEventPublisher([sink]))
        workspace = WorkspaceContext(uuid4(), ROOT)
        session = SessionContext(uuid4(), "default", workspace)
        run_context = AgentRunContext("run-1", session, 3, RunLimits())

        set_run_event_context(run_context)
        emit_event("turn_started", "test")

        event = sink.events[0]
        self.assertEqual(workspace.workspace_id, event.workspace_id)
        self.assertEqual(session.session_id, event.session_id)
        self.assertEqual(3, event.turn_index)
        self.assertEqual("run-1", event.run_id)

    def test_event_context_can_be_restored_after_scoped_work(self):
        set_event_context(run_id="outer")
        token = set_event_context(run_id="inner")
        self.assertEqual("inner", get_event_context().run_id)

        reset_event_context(token)

        self.assertEqual("outer", get_event_context().run_id)

    def test_event_publisher_isolates_failing_subscribers(self):
        sink = MemorySink()
        publisher = SinkEventPublisher([FailingSink(), sink])

        set_event_publisher(publisher)
        event = emit_event("demo", "test")

        self.assertEqual([event], sink.events)

    def test_streaming_stops_when_tool_call_limit_is_exceeded(self):
        workspace = WorkspaceContext(uuid4(), ROOT)
        session = SessionContext(uuid4(), "default", workspace)
        run_context = AgentRunContext(
            "run-1",
            session,
            1,
            RunLimits(max_graph_steps=10, max_tool_calls=1, max_subagent_steps=10),
        )
        graph = FakeGraph(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "one", "args": {}, "id": "1", "type": "tool_call"},
                        {"name": "two", "args": {}, "id": "2", "type": "tool_call"},
                    ],
                )
            ]
        )

        events = list(stream_graph_events(graph, [HumanMessage(content="go")], run_context))

        self.assertEqual("error", events[-1]["event"])
        self.assertEqual(StopReason.TOOL_CALL_LIMIT.value, events[-1]["data"]["stop_reason"])


if __name__ == "__main__":
    unittest.main()
