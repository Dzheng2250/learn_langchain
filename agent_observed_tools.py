import time
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode

from agent_hooks import record_tool_failed, record_tool_finished, record_tool_started


def _tool_call_name(request) -> str | None:
    return request.tool_call.get("name")


def _tool_call_id(request) -> str | None:
    return request.tool_call.get("id")


def _tool_call_args(request):
    return request.tool_call.get("args")


def _result_preview(result) -> str:
    """Return a compact, generic preview for ToolNode results."""
    if isinstance(result, list):
        return "\n".join(_result_preview(item) for item in result)

    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    if content is not None:
        return repr(content)
    return repr(result)


def _result_is_error(result) -> bool:
    if isinstance(result, list):
        return any(_result_is_error(item) for item in result)
    return getattr(result, "status", None) == "error"


def _observe_tool_call(source: str, request, execute: Callable[[Any], Any]):
    tool = _tool_call_name(request)
    tool_call_id = _tool_call_id(request)
    started_at = time.monotonic()

    record_tool_started(
        source,
        tool=tool,
        tool_call_id=tool_call_id,
        args=_tool_call_args(request),
    )

    try:
        result = execute(request)
    except Exception as exc:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        record_tool_failed(
            source,
            tool=tool,
            tool_call_id=tool_call_id,
            error=exc,
            duration_ms=duration_ms,
        )
        raise

    duration_ms = int((time.monotonic() - started_at) * 1000)
    preview = _result_preview(result)
    if _result_is_error(result):
        record_tool_failed(
            source,
            tool=tool,
            tool_call_id=tool_call_id,
            payload={
                "content_preview": preview,
                "content_chars": len(preview),
            },
            duration_ms=duration_ms,
        )
    else:
        record_tool_finished(
            source,
            tool=tool,
            tool_call_id=tool_call_id,
            content=preview,
            duration_ms=duration_ms,
        )
    return result


class ObservedToolNode(ToolNode):
    """ToolNode with centralized tool boundary hook events."""

    def __init__(
        self,
        tools: Sequence[BaseTool | Callable],
        *,
        event_source: str = "agent_tool_node",
        **kwargs,
    ) -> None:
        existing_wrapper = kwargs.pop("wrap_tool_call", None)

        def observed_wrapper(request, execute):
            if existing_wrapper is None:
                return _observe_tool_call(event_source, request, execute)

            def wrapped_execute(observed_request):
                return existing_wrapper(observed_request, execute)

            return _observe_tool_call(event_source, request, wrapped_execute)

        super().__init__(tools, wrap_tool_call=observed_wrapper, **kwargs)
