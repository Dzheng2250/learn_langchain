import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from tests.support.paths import REPOSITORY_ROOT

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from src.core.agent.graph import create_parent_graph
from src.core.agent.models import AgentRunContext, RunLimits, StopReason
from src.core.context.manager import AgentContextManager
from src.core.telemetry import (
    BaseEventSink,
    EventBus,
    bind_context,
    bind_run_context,
    current_context,
    emit_event,
    install_event_bus,
    reset_context,
)
from src.core.llm.provider import AnthropicProvider, LlmPurpose
from src.core.memory.extractor import MemoryCandidateExtractor
from src.core.streaming.events import stream_graph_events
from src.core.tools.catalog import ToolAudience, ToolRegistry, ToolRisk, ToolSpec
from src.core.workspace.models import SessionContext, WorkspaceContext


ROOT = REPOSITORY_ROOT


class FakeModel:
    def __init__(self):
        self.received_configs = []

    def invoke(self, _messages, config=None):
        self.received_configs.append(config)
        return AIMessage(content="[]")


class RecordingProvider:
    def __init__(self):
        self.calls = []
        self.models = []

    def create_chat_model(self, purpose, **kwargs):
        self.calls.append((purpose, kwargs))
        model = FakeModel()
        self.models.append(model)
        return model


class MemorySink(BaseEventSink):
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class FailingSink(BaseEventSink):
    def emit(self, _event):
        raise RuntimeError("sink failed")


class FakeGraph:
    def __init__(self, messages):
        self.messages = messages

    def stream(self, inputs, **_kwargs):
        yield "values", {"messages": [*inputs["messages"], *self.messages]}


class AgentExecutionArchitectureTest(unittest.TestCase):
    def tearDown(self):
        install_event_bus(None)
        bind_context()

    def test_model_provider_is_shared_by_non_agent_llm_workloads(self):
        provider = RecordingProvider()

        AgentContextManager(model_provider=provider)._create_summary_llm()
        MemoryCandidateExtractor(provider)._create_llm()

        self.assertEqual(
            [LlmPurpose.CONTEXT_SUMMARY, LlmPurpose.MEMORY_EXTRACTION],
            [purpose for purpose, _kwargs in provider.calls],
        )

    def test_anthropic_provider_owns_default_vendor_model_construction(self):
        bound = object()
        model = Mock()
        model.bind_tools.return_value = bound
        with patch("src.core.llm.provider.ChatAnthropic", return_value=model) as constructor:
            result = AnthropicProvider(
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
        self.assertEqual("test-model", constructor.call_args.kwargs["model"])
        self.assertEqual("key", constructor.call_args.kwargs["api_key"])
        self.assertEqual("https://example.test", constructor.call_args.kwargs["base_url"])
        self.assertTrue(constructor.call_args.kwargs["streaming"])
        self.assertEqual(0.5, constructor.call_args.kwargs["temperature"])
        self.assertEqual(
            {"purpose": LlmPurpose.PARENT_AGENT.value},
            constructor.call_args.kwargs["metadata"],
        )
        self.assertEqual(0, constructor.call_args.kwargs["max_retries"])
        model.bind_tools.assert_called_once_with(["tool"])

    def test_anthropic_provider_does_not_bind_tools_when_absent(self):
        model = Mock()
        with patch("src.core.llm.provider.ChatAnthropic", return_value=model) as constructor:
            result = AnthropicProvider(
                model="test-model",
                api_key="key",
            ).create_chat_model(LlmPurpose.MEMORY_EXTRACTION, streaming=False)

        self.assertIs(model, result)
        self.assertFalse(constructor.call_args.kwargs["streaming"])
        model.bind_tools.assert_not_called()

    def test_provider_reports_missing_api_key_without_network_request(self):
        status = AnthropicProvider(
            model="test-model",
            api_key="",
            base_url="https://example.test",
        ).configuration_status()

        self.assertFalse(status.configured)
        self.assertEqual(("LEARN_AGENT_LLM_API_KEY",), status.missing)

    def test_provider_reports_configured_with_generic_api_key(self):
        status = AnthropicProvider(
            model="test-model",
            api_key="configured",
        ).configuration_status()

        self.assertTrue(status.configured)
        self.assertEqual((), status.missing)

    def test_provider_reports_missing_model_without_using_vendor_default(self):
        status = AnthropicProvider(
            model="",
            api_key="configured",
        ).configuration_status()

        self.assertFalse(status.configured)
        self.assertEqual(("LEARN_AGENT_MODEL",), status.missing)

    def test_parent_graph_passes_runnable_config_to_model_call(self):
        provider = RecordingProvider()
        graph = create_parent_graph([], "", provider)

        list(stream_graph_events(graph, [HumanMessage(content="hello")]))

        self.assertTrue(provider.models)
        self.assertIsNotNone(provider.models[0].received_configs[0])

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
        install_event_bus(EventBus([sink]))
        workspace = WorkspaceContext(uuid4(), ROOT)
        session = SessionContext(uuid4(), "default", workspace)
        run_context = AgentRunContext("run-1", session, 3, RunLimits())

        bind_run_context(run_context)
        emit_event("turn_started", "test")

        event = sink.events[0]
        self.assertEqual(workspace.workspace_id, event.workspace_id)
        self.assertEqual(session.session_id, event.session_id)
        self.assertEqual(3, event.turn_index)
        self.assertEqual("run-1", event.run_id)

    def test_event_context_can_be_restored_after_scoped_work(self):
        bind_context(run_id="outer")
        token = bind_context(run_id="inner")
        self.assertEqual("inner", current_context().run_id)

        reset_context(token)

        self.assertEqual("outer", current_context().run_id)

    def test_event_publisher_isolates_failing_subscribers(self):
        sink = MemorySink()
        publisher = EventBus([FailingSink(), sink])

        install_event_bus(publisher)
        event = emit_event("demo", "test")

        self.assertEqual([event], sink.events)

    def test_stop_hook_rejection_uses_rejected_error_path(self):
        source = (ROOT / "src/core/agent/loop.py").read_text(encoding="utf-8")

        self.assertIn("raise HookRejected(", source)
        self.assertNotIn(
            "raise RuntimeError(" + chr(10) + "                            stop_decision.reason",
            source,
        )
        rejected_handler = source.index("except HookRejected as exc:")
        unexpected_handler = source.index("except Exception as exc:")
        self.assertLess(rejected_handler, unexpected_handler)
        self.assertIn(
            "stream_rejected_exception",
            source[rejected_handler:unexpected_handler],
        )

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
