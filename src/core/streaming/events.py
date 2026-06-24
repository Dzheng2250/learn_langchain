"""Adapt LangGraph streams into stable request-level Agent events."""

from langchain_core.messages import AIMessageChunk
from langchain_core.messages import HumanMessage
from langgraph.errors import GraphRecursionError

from src.core.common.content import message_content_text

from src.core.agent.models import AgentRunContext, RunLimits, StopReason
from src.core.errors import ProviderErrorHandler
from src.core.streaming.failures import graph_failure_event
from src.core.streaming.message_events import step_events_from_message, tool_calls_from_message
from src.core.telemetry import emit_event
from src.core.agent.budget import ToolBudgetExceeded
from src.core.llm.retry_context import (
    current_attempt_id,
    mark_attempt_output_emitted,
)


def stream_graph_events(
    app,
    input_messages: list | None,
    run_context: AgentRunContext | None = None,
    *,
    checkpoint_thread_id: str | None = None,
    provider_error_handler: ProviderErrorHandler | None = None,
    tool_context=None,
):
    """Yield events for one Slice; step-limit exhaustion remains recoverable."""
    limits = run_context.limits if run_context else RunLimits()
    inputs = {"messages": input_messages} if input_messages is not None else None
    final_state = None
    config = {"recursion_limit": limits.max_graph_steps}
    if checkpoint_thread_id:
        config["configurable"] = {"thread_id": checkpoint_thread_id}
    # Values snapshots contain the whole message state. Track the previous
    # length so each completed message emits a step event exactly once.
    if inputs is None and checkpoint_thread_id:
        seen_message_count = len(app.get_state(config).values.get("messages", []))
    else:
        seen_message_count = len((inputs or {}).get("messages", []))
    tool_call_count = 0
    graph_steps_used = 0

    yield {
        "event": "step",
        "data": {
            "type": "agent_start",
            "message": "Agent turn started.",
        },
    }

    try:
        stream_options = {
            "config": config,
            "stream_mode": ["messages", "values"],
        }
        if checkpoint_thread_id:
            stream_options["durability"] = "sync"
        if tool_context is not None:
            stream_options["context"] = tool_context
        for stream_mode, chunk in app.stream(inputs, **stream_options):
            if stream_mode == "messages":
                message_chunk, _metadata = chunk
                if isinstance(message_chunk, AIMessageChunk) and message_content_text(message_chunk):
                    mark_attempt_output_emitted()
                    attempt_id = current_attempt_id()
                    yield {
                        "event": "token",
                        "data": {
                            "content": message_content_text(message_chunk),
                            **({"attempt_id": attempt_id} if attempt_id else {}),
                        },
                    }
            elif stream_mode == "values":
                graph_steps_used += 1
                final_state = chunk
                state_messages = chunk.get("messages", [])
                new_messages = state_messages[seen_message_count:]
                seen_message_count = len(state_messages)

                for message in new_messages:
                    tool_calls = tool_calls_from_message(message)
                    tool_call_count += len(tool_calls)
                    if tool_call_count > limits.max_tool_calls:
                        emit_event(
                            "tool_call_limit",
                            "agent_stream",
                            "Agent exceeded the per-turn tool call limit.",
                            {
                                "tool_call_count": tool_call_count,
                                "max_tool_calls": limits.max_tool_calls,
                            },
                            level="error",
                        )
                        yield {
                            "event": "error",
                            "data": {
                                "type": StopReason.TOOL_CALL_LIMIT.value,
                                "stop_reason": StopReason.TOOL_CALL_LIMIT.value,
                                "message": (
                                    "Agent exceeded the per-turn tool call limit "
                                    f"({limits.max_tool_calls})."
                                ),
                                "graph_steps_used": graph_steps_used,
                            },
                        }
                        return
                    yield from step_events_from_message(message)
    except GraphRecursionError:
        emit_event(
            "recursion_limit",
            "agent_stream",
            f"Graph exceeded recursion_limit={limits.max_graph_steps}.",
            {"recursion_limit": limits.max_graph_steps},
            level="error",
        )
        yield {
            "event": "paused",
            "data": {
                "type": StopReason.GRAPH_STEP_LIMIT.value,
                "stop_reason": StopReason.GRAPH_STEP_LIMIT.value,
                "message": f"Graph exceeded recursion_limit={limits.max_graph_steps}.",
                "tool_call_count": tool_call_count,
                "graph_steps_used": graph_steps_used,
            },
        }
        return
    except ToolBudgetExceeded as exc:
        emit_event(
            "execution_budget_exhausted",
            "agent_stream",
            str(exc),
            level="warning",
        )
        yield {
            "event": "paused",
            "data": {
                "type": StopReason.BUDGET_LIMIT.value,
                "stop_reason": StopReason.BUDGET_LIMIT.value,
                "message": str(exc),
                "tool_call_count": tool_call_count,
                "graph_steps_used": graph_steps_used,
            },
        }
        return
    except Exception as exc:
        yield graph_failure_event(
            exc,
            graph_steps_used=graph_steps_used,
            provider_error_handler=provider_error_handler,
        )
        return

    yield {
        "event": "done",
        "data": {
            "messages": final_state["messages"] if final_state is not None else input_messages,
            "stop_reason": StopReason.COMPLETED.value,
            "tool_call_count": tool_call_count,
            "graph_steps_used": graph_steps_used,
        },
    }


def stream_agent_events(app, messages: list, user_input: str):
    """Yield events for one user turn using raw recent messages."""
    input_messages = [*messages, HumanMessage(content=user_input)]
    yield from stream_graph_events(app, input_messages)
