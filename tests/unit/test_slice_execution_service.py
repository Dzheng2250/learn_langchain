import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.core.agent.budget import ExecutionBudget
from src.core.agent.models import AgentRunContext, RunLimits, StopReason
from src.core.agent.slices import SliceExecutionService
from src.core.errors import ProviderErrorHandler
from src.core.context.compaction import ContextCompactionRequired
from src.core.state.types import ExecutionStatus
from src.core.tasks.context import ToolExecutionContext
from src.core.workspace.models import SessionContext, WorkspaceContext


class FakeExecution:
    execution_id = "exec-1"
    grant_index = 2


class FakeExecutionRepository:
    def __init__(self):
        self.started = []
        self.finished = []

    def start_slice(self, execution_id, grant_index, slice_number):
        self.started.append((execution_id, grant_index, slice_number))
        return f"slice-{slice_number}"

    def finish_slice(self, slice_id, execution_id, **kwargs):
        self.finished.append((slice_id, execution_id, kwargs))


def _consume(generator):
    yielded = []
    while True:
        try:
            yielded.append(next(generator))
        except StopIteration as exc:
            return yielded, exc.value


class SliceExecutionServiceTest(unittest.TestCase):
    def _run_context(self):
        session = SessionContext(
            session_id=uuid4(),
            session_name="default",
            workspace=WorkspaceContext(uuid4(), Path("workspace")),
        )
        return AgentRunContext(
            run_id="run-1",
            session=session,
            turn_index=1,
            limits=RunLimits(),
        )

    def _tool_context(self):
        return ToolExecutionContext(
            workspace_id=str(uuid4()),
            session_id=str(uuid4()),
            execution_id="exec-1",
        )

    def _stream(self, repository, events):
        service = SliceExecutionService(execution_store=repository, provider_error_handler=ProviderErrorHandler())
        with patch("src.core.agent.slices.stream_graph_events", return_value=iter(events)):
            return _consume(
                service.stream_slice(
                    graph=object(),
                    slice_input=["input"],
                    run_context=self._run_context(),
                    execution=FakeExecution(),
                    slice_number=1,
                    checkpoint_thread_id="thread-1",
                    budget=ExecutionBudget(),
                    tool_context=self._tool_context(),
                )
            )

    def test_tool_context_receives_the_started_slice_id(self):
        repository = FakeExecutionRepository()
        service = SliceExecutionService(
            execution_store=repository,
            provider_error_handler=ProviderErrorHandler(),
        )
        with patch("src.core.agent.slices.stream_graph_events", return_value=iter([])) as stream:
            _consume(service.stream_slice(
                graph=object(),
                slice_input=["input"],
                run_context=self._run_context(),
                execution=FakeExecution(),
                slice_number=1,
                checkpoint_thread_id="thread-1",
                budget=ExecutionBudget(),
                tool_context=self._tool_context(),
            ))

        self.assertEqual("slice-1", stream.call_args.kwargs["tool_context"].slice_id)

    def test_paused_slice_finishes_as_budget_pause(self):
        repository = FakeExecutionRepository()
        events = [
            {
                "event": "paused",
                "data": {
                    "stop_reason": StopReason.GRAPH_STEP_LIMIT.value,
                    "graph_steps_used": 12,
                    "tool_call_count": 3,
                },
            }
        ]

        yielded, result = self._stream(repository, events)

        self.assertEqual([], yielded)
        self.assertTrue(result.paused_for_budget)
        self.assertEqual(3, result.tool_call_count)
        self.assertEqual([("exec-1", 2, 1)], repository.started)
        self.assertEqual(ExecutionStatus.PAUSED_BUDGET, repository.finished[0][2]["status"])
        self.assertEqual(12, repository.finished[0][2]["graph_steps_used"])

    def test_error_slice_finishes_as_error_and_returns_error_item(self):
        repository = FakeExecutionRepository()
        error_item = {
            "event": "error",
            "data": {
                "stop_reason": StopReason.TURN_ERROR.value,
                "graph_steps_used": 5,
                "message": "failed",
            },
        }

        yielded, result = self._stream(repository, [error_item])

        self.assertEqual([], yielded)
        self.assertIs(error_item, result.error_item)
        self.assertEqual(ExecutionStatus.PAUSED_ERROR, repository.finished[0][2]["status"])
        self.assertEqual(5, repository.finished[0][2]["graph_steps_used"])

    def test_done_slice_returns_done_item_without_finishing_slice(self):
        repository = FakeExecutionRepository()
        done_item = {
            "event": "done",
            "data": {
                "messages": ["message"],
                "graph_steps_used": 1,
                "tool_call_count": 1,
            },
        }

        yielded, result = self._stream(repository, [done_item])

        self.assertEqual([], yielded)
        self.assertIs(done_item, result.done_item)
        self.assertEqual(1, result.tool_call_count)
        self.assertEqual([], repository.finished)

    def test_context_compaction_pause_preserves_recoverable_slice_status(self):
        repository = FakeExecutionRepository()
        service = SliceExecutionService(
            execution_store=repository,
            provider_error_handler=ProviderErrorHandler(),
        )

        def failing_events():
            raise ContextCompactionRequired("compact before continuing")
            yield

        with patch("src.core.agent.slices.stream_graph_events", return_value=failing_events()):
            with self.assertRaises(ContextCompactionRequired):
                _consume(service.stream_slice(
                    graph=object(),
                    slice_input=["input"],
                    run_context=self._run_context(),
                    execution=FakeExecution(),
                    slice_number=1,
                    checkpoint_thread_id="thread-1",
                    budget=ExecutionBudget(),
                    tool_context=self._tool_context(),
                ))

        finished = repository.finished[0][2]
        self.assertEqual(ExecutionStatus.PAUSED_RECOVERY, finished["status"])
        self.assertEqual(
            StopReason.CONTEXT_COMPACTION_REQUIRED.value,
            finished["stop_reason"],
        )


if __name__ == "__main__":
    unittest.main()
