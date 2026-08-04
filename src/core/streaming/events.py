"""Adapt LangGraph streams into stable request-level Agent events."""

from langchain_core.messages import AIMessageChunk
from langchain_core.messages import HumanMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from src.config.settings import REASONING_DISPLAY, REASONING_PREVIEW_LIMIT
from src.core.common.content import message_content_text, reasoning_content_text

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
from src.core.llm.completion import ModelOutputLimitError, response_stop_reason


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
    resume_command = isinstance(input_messages, Command)
    inputs = (
        input_messages
        if resume_command
        else (
            {
                "messages": input_messages,
                "turn_journal": [input_messages[-1]] if input_messages else [],
            }
            if input_messages is not None else None
        )
    )
    final_state = None
    config = {"recursion_limit": limits.max_graph_steps}
    if checkpoint_thread_id:
        config["configurable"] = {"thread_id": checkpoint_thread_id}
    # Values snapshots contain the whole message state. Track the previous
    # length so each completed message emits a step event exactly once.
    if (inputs is None or resume_command) and checkpoint_thread_id:
        snapshot_values = app.get_state(config).values
        seen_message_count = len(
            snapshot_values.get("turn_journal", snapshot_values.get("messages", []))
        )
    else:
        seen_message_count = len(
            (inputs or {}).get("turn_journal", (inputs or {}).get("messages", []))
        )
    tool_call_count = 0
    graph_steps_used = 0
    reasoning_display = _reasoning_display()
    reasoning_started = False
    reasoning_stream_seen = False
    reasoning_chars = 0
    reasoning_redacted = False

    def finish_reasoning():
        nonlocal reasoning_started, reasoning_chars, reasoning_redacted
        if not reasoning_started:
            return []
        event = _reasoning_event(
            "reasoning_finished",
            char_count=reasoning_chars,
            redacted=reasoning_redacted,
            attempt_id=current_attempt_id(),
            display=reasoning_display,
        )
        reasoning_started = False
        reasoning_chars = 0
        reasoning_redacted = False
        return [event]

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
                if isinstance(message_chunk, AIMessageChunk):
                    reasoning_text, redacted, raw_chars = reasoning_content_text(
                        message_chunk,
                        preview_limit=REASONING_PREVIEW_LIMIT,
                    )
                    if reasoning_display != "hidden" and (reasoning_text or redacted):
                        attempt_id = current_attempt_id()
                        if not reasoning_started:
                            reasoning_started = True
                            yield _reasoning_event(
                                "reasoning_started",
                                attempt_id=attempt_id,
                                display=reasoning_display,
                            )
                        reasoning_stream_seen = True
                        reasoning_chars += raw_chars
                        reasoning_redacted = reasoning_redacted or redacted
                        if reasoning_display in {"collapsed", "expanded"} and reasoning_text:
                            yield _reasoning_event(
                                "reasoning_delta",
                                content=reasoning_text,
                                char_count=reasoning_chars,
                                redacted=redacted,
                                attempt_id=attempt_id,
                                display=reasoning_display,
                            )
                    text = message_content_text(message_chunk)
                    if text:
                        yield from finish_reasoning()
                        mark_attempt_output_emitted()
                        attempt_id = current_attempt_id()
                        yield {
                            "event": "token",
                            "data": {
                                "content": text,
                                **({"attempt_id": attempt_id} if attempt_id else {}),
                            },
                        }
            elif stream_mode == "values":
                graph_steps_used += 1
                final_state = chunk
                state_messages = chunk.get("turn_journal", chunk.get("messages", []))
                new_messages = state_messages[seen_message_count:]
                seen_message_count = len(state_messages)

                for message in new_messages:
                    if not reasoning_stream_seen:
                        yield from reasoning_events_from_message(
                            message,
                            display=reasoning_display,
                            attempt_id=current_attempt_id(),
                        )
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
                        yield from finish_reasoning()
                        return
                    yield from step_events_from_message(message)
        if checkpoint_thread_id and hasattr(app, "get_state"):
            snapshot = app.get_state(config)
            interrupts = tuple(getattr(snapshot, "interrupts", ()) or ())
            if interrupts:
                payload = interrupts[0].value
                request = payload.get("request", {}) if isinstance(payload, dict) else {}
                yield {
                    "event": "tool_approval_required",
                    "data": request,
                }
                yield {
                    "event": "paused",
                    "data": {
                        "type": StopReason.TOOL_APPROVAL.value,
                        "stop_reason": StopReason.TOOL_APPROVAL.value,
                        "message": "Tool execution is waiting for approval.",
                        "approval_request": request,
                        "tool_call_count": tool_call_count,
                        "graph_steps_used": graph_steps_used,
                    },
                }
                yield from finish_reasoning()
                return
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
        yield from finish_reasoning()
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
        yield from finish_reasoning()
        return
    except Exception as exc:
        yield graph_failure_event(
            exc,
            graph_steps_used=graph_steps_used,
            provider_error_handler=provider_error_handler,
        )
        yield from finish_reasoning()
        return

    yield from finish_reasoning()

    active_messages = final_state.get("messages", []) if final_state is not None else []
    final_messages = (
        final_state.get("turn_journal", active_messages)
        if final_state is not None else []
    )
    if active_messages and response_stop_reason(active_messages[-1]) == "max_tokens":
        yield graph_failure_event(
            ModelOutputLimitError(
                "The model exhausted its output token budget before producing a "
                "complete response. Increase LEARN_AGENT_LLM_MAX_TOKENS and resume."
            ),
            graph_steps_used=graph_steps_used,
            provider_error_handler=provider_error_handler,
        )
        return

    yield {
        "event": "done",
        "data": {
            "messages": final_messages if final_state is not None else input_messages,
            "stop_reason": StopReason.COMPLETED.value,
            "tool_call_count": tool_call_count,
            "graph_steps_used": graph_steps_used,
        },
    }


def stream_agent_events(app, messages: list, user_input: str):
    """Yield events for one user turn using raw recent messages."""
    input_messages = [*messages, HumanMessage(content=user_input)]
    yield from stream_graph_events(app, input_messages)


def _reasoning_display() -> str:
    """Return a supported frontend reasoning display mode."""
    if REASONING_DISPLAY in {"metadata", "collapsed", "expanded", "hidden"}:
        return REASONING_DISPLAY
    return "metadata"


def _reasoning_event(
    event: str,
    *,
    content: str = "",
    char_count: int = 0,
    redacted: bool = False,
    attempt_id: str | None = None,
    display: str = "metadata",
) -> dict:
    data = {
        "source": "parent_agent",
        "char_count": char_count,
        "redacted": redacted,
        "display": display,
        "expanded": display == "expanded",
    }
    if attempt_id:
        data["attempt_id"] = attempt_id
    if content and display in {"collapsed", "expanded"}:
        data["content"] = content
    return {"event": event, "data": data}


def reasoning_events_from_message(
    message,
    *,
    display: str | None = None,
    attempt_id: str | None = None,
) -> list[dict]:
    """Create reasoning events from a completed non-streaming message snapshot."""
    mode = display or _reasoning_display()
    if mode == "hidden":
        return []
    text, redacted, raw_chars = reasoning_content_text(
        message,
        preview_limit=REASONING_PREVIEW_LIMIT,
    )
    if not text and not redacted:
        return []
    events = [_reasoning_event("reasoning_started", attempt_id=attempt_id, display=mode)]
    if mode in {"collapsed", "expanded"} and text:
        events.append(
            _reasoning_event(
                "reasoning_delta",
                content=text,
                char_count=raw_chars,
                redacted=redacted,
                attempt_id=attempt_id,
                display=mode,
            )
        )
    events.append(
        _reasoning_event(
            "reasoning_finished",
            char_count=raw_chars,
            redacted=redacted,
            attempt_id=attempt_id,
            display=mode,
        )
    )
    return events
