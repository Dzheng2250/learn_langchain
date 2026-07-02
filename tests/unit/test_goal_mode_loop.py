import unittest
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from src.core.agent.loop import LoopConfig, TurnExecutionLoop
from src.core.agent.models import RunLimits
from src.core.agent.slices import SliceExecutionResult
from src.core.prompts.goal_mode import (
    completion_review_message,
    inject_goal_mode_prompt,
)
from src.core.streaming.events import stream_graph_events


class _Coordinator:
    def __init__(self):
        self.finalized = None

    def prepare(self, *, session, user_input, run_id, limits):
        return SimpleNamespace(
            state=SimpleNamespace(),
            turn_index=1,
            run_context=SimpleNamespace(
                run_id=run_id,
                workspace=session.workspace,
                session=session,
                turn_index=1,
            ),
            input_messages=[HumanMessage(content=user_input)],
        )

    def finalize(self, **kwargs):
        self.finalized = kwargs
        return SimpleNamespace(
            maintenance_status="pending",
            memory_status="not_scheduled",
            memory_request_explicit=False,
        )


class _Slices:
    def __init__(self):
        self.inputs = []
        self.finished = []

    def stream_slice(self, **kwargs):
        self.inputs.append(kwargs["slice_input"])
        if len(self.inputs) == 1:
            messages = [
                *kwargs["slice_input"],
                AIMessage(content="premature answer"),
            ]
        else:
            messages = [
                *self.inputs[0],
                AIMessage(content="premature answer"),
                *kwargs["slice_input"],
                AIMessage(content="final answer"),
            ]
        if False:
            yield None
        return SliceExecutionResult(
            slice_id=f"slice-{len(self.inputs)}",
            done_item={
                "data": {
                    "messages": messages,
                    "graph_steps_used": len(self.inputs),
                }
            },
        )

    def finish_for_goal_continuation(self, **kwargs):
        self.finished.append(kwargs)


class _Observer:
    def __init__(self):
        self.finished = []

    def run_started(self, *_args):
        pass

    def slice_finished(self, slice_id):
        self.finished.append(slice_id)

    def run_finished(self, *_args):
        pass


class _Tasks:
    def __init__(self):
        self.calls = 0

    def has_unfinished(self, _context):
        self.calls += 1
        return True


class GoalModeLoopTest(unittest.TestCase):
    def test_real_checkpoint_graph_accepts_review_message_list(self):
        builder = StateGraph(MessagesState)

        def agent_node(_state):
            return {"messages": [AIMessage(content="ok")]}

        builder.add_node("agent", agent_node)
        builder.add_edge(START, "agent")
        builder.add_edge("agent", END)
        graph = builder.compile(checkpointer=MemorySaver())
        run_context = SimpleNamespace(limits=RunLimits(max_graph_steps=10))

        first = list(stream_graph_events(
            graph,
            [HumanMessage(content="request")],
            run_context,
            checkpoint_thread_id="goal-review-thread",
        ))
        second = list(stream_graph_events(
            graph,
            [completion_review_message()],
            run_context,
            checkpoint_thread_id="goal-review-thread",
        ))

        self.assertEqual("done", first[-1]["event"])
        self.assertEqual("done", second[-1]["event"])
        self.assertEqual("ok", second[-1]["data"]["messages"][-1].content)
    def test_unfinished_goal_plan_triggers_one_synthetic_review(self):
        coordinator = _Coordinator()
        slices = _Slices()
        observer = _Observer()
        tasks = _Tasks()
        loop = TurnExecutionLoop(
            turn_coordinator=coordinator,
            run_limits=SimpleNamespace(),
            slice_execution_service=slices,
            observer=observer,
            error_handler=SimpleNamespace(),
            pause_handler=SimpleNamespace(),
            config=LoopConfig(max_auto_slices=3),
            task_service=tasks,
        )
        session = SimpleNamespace(
            workspace=SimpleNamespace(workspace_id="workspace-1", root="."),
            session_id="session-1",
            session_name="default",
        )
        execution = SimpleNamespace(
            execution_id="execution-1",
            checkpoint_thread_id="thread-1",
            goal_mode=True,
        )

        events = list(loop.stream_locked_turn(
            session,
            graph=SimpleNamespace(),
            user_input="original request",
            model_user_input=inject_goal_mode_prompt("hook rewritten request"),
            run_id="run-1",
            execution=execution,
        ))

        self.assertEqual(2, len(slices.inputs))
        self.assertEqual(1, tasks.calls)
        self.assertEqual("slice-1", slices.finished[0]["slice_id"])
        self.assertEqual(["slice-1", "slice-2"], observer.finished)
        self.assertEqual("goal_continuation_started", events[0]["event"])
        self.assertEqual("done", events[-1]["event"])
        self.assertEqual(
            "goal_completion_review",
            slices.inputs[1][0].additional_kwargs[
                "learn_agent_internal_prompt"
            ],
        )
        committed = coordinator.finalized["final_messages"]
        self.assertEqual(
            ["original request", "premature answer", "final answer"],
            [message.content for message in committed],
        )


if __name__ == "__main__":
    unittest.main()