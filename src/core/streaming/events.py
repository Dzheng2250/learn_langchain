"""Adapt LangGraph streams into stable request-level Agent events."""

from langchain_core.messages import AIMessageChunk
from langchain_core.messages import HumanMessage
from langgraph.errors import GraphRecursionError

from src.core.agent.models import AgentRunContext, RunLimits, StopReason
from src.core.errors import ProviderErrorHandler
from src.core.telemetry import emit_event, record_error
from src.core.agent.budget import ToolBudgetExceeded


def _message_text(message) -> str:
    """Return message content as text for event payloads."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return repr(content)


def _message_preview(message, limit: int = 600) -> str:
    """Return a short content preview for step events."""
    text = _message_text(message)
    if len(text) > limit:
        return text[:limit] + "\n... truncated ..."
    return text


def _step_events_from_message(message) -> list[dict]:
    """Convert completed graph messages into step-level events."""
    message_type = message.__class__.__name__

    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        return [
            {
                "event": "step",
                "data": {
                    "type": "tool_call_start",
                    "tool": tool_call.get("name"),
                    "args": tool_call.get("args"),
                    "id": tool_call.get("id"),
                },
            }
            for tool_call in tool_calls
        ]

    if message_type == "ToolMessage":
        return [
            {
                "event": "step",
                "data": {
                    "type": "tool_call_result",
                    "tool": getattr(message, "name", None),
                    "tool_call_id": getattr(message, "tool_call_id", None),
                    "content": _message_preview(message),
                },
            }
        ]

    if message_type == "AIMessage":
        return [
            {
                "event": "step",
                "data": {
                    "type": "agent_message",
                    "content": _message_preview(message),
                },
            }
        ]

    return []


def stream_graph_events(
    app,
    input_messages: list | None,
    run_context: AgentRunContext | None = None,
    *,
    checkpoint_thread_id: str | None = None,
    provider_error_handler: ProviderErrorHandler | None = None,
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
        for stream_mode, chunk in app.stream(inputs, **stream_options):
            if stream_mode == "messages":
                message_chunk, _metadata = chunk
                if isinstance(message_chunk, AIMessageChunk) and message_chunk.content:
                    yield {
                        "event": "token",
                        "data": {
                            "content": message_chunk.content,
                        },
                    }
            elif stream_mode == "values":
                graph_steps_used += 1
                final_state = chunk
                state_messages = chunk.get("messages", [])
                new_messages = state_messages[seen_message_count:]
                seen_message_count = len(state_messages)

                for message in new_messages:
                    tool_calls = getattr(message, "tool_calls", None) or []
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
                    yield from _step_events_from_message(message)
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
        resolution = (provider_error_handler or ProviderErrorHandler()).resolve(exc)
        record_error(
            "agent_stream",
            "llm_or_graph",
            RuntimeError(resolution.public_message),
            "Graph execution failed.",
            resolution.event_data(),
            event_type="llm_or_graph_failed",
        )
        yield {
            "event": "error",
            "data": {
                "type": "provider_error",
                "stop_reason": StopReason.GRAPH_ERROR.value,
                "message": resolution.public_message,
                "graph_steps_used": graph_steps_used,
                **resolution.event_data(),
            },
        }
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
