"""Central ToolNode wrapper that observes every tool-call boundary."""

import time
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode

from src.core.common.content import message_content_text

from src.core.hooks import HookAction, HookContext, HookPoint, NOOP_HOOK_DISPATCHER
from src.core.telemetry import record_tool_failed, record_tool_finished, record_tool_started
from src.core.agent.budget import ToolBudgetExceeded, current_execution_budget
from src.core.tools.catalog import ToolRisk


def _tool_call_name(request) -> str | None:
    """Read the model-requested tool name from a ToolNode request."""
    return request.tool_call.get("name")


def _tool_call_id(request) -> str | None:
    """Read the correlation ID assigned to one tool call."""
    return request.tool_call.get("id")


def _tool_call_args(request):
    """Read validated tool arguments from a ToolNode request."""
    return request.tool_call.get("args")


def _result_preview(result) -> str:
    """Return a compact, generic preview for ToolNode results."""
    if isinstance(result, list):
        return "\n".join(_result_preview(item) for item in result)

    content = message_content_text(result)
    if content:
        return content
    return repr(result)


def _result_is_error(result) -> bool:
    """Detect ToolMessage error status across scalar or batched results."""
    if isinstance(result, list):
        return any(_result_is_error(item) for item in result)
    return getattr(result, "status", None) == "error"


def _tool_error_message(request, exc: Exception) -> ToolMessage:
    """Convert a tool implementation failure into a model-visible tool error."""
    tool = _tool_call_name(request) or "unknown"
    tool_call_id = _tool_call_id(request) or ""
    return ToolMessage(
        content=f"Tool {tool} failed: {type(exc).__name__}: {exc}",
        name=tool,
        tool_call_id=tool_call_id,
        status="error",
    )


def _tool_denied_message(request, reason: str) -> ToolMessage:
    """Convert a hook rejection into a model-visible tool denial."""
    tool = _tool_call_name(request) or "unknown"
    tool_call_id = _tool_call_id(request) or ""
    return ToolMessage(
        content=f"Tool {tool} was denied: {reason}",
        name=tool,
        tool_call_id=tool_call_id,
        status="error",
        additional_kwargs={"tool_execution_status": "denied"},
    )


def _observe_tool_call(
    source: str,
    request,
    execute: Callable[[Any], Any],
    risk_by_name: dict[str, ToolRisk] | None = None,
):
    """Execute one tool call while recording start, success, or failure."""
    tool = _tool_call_name(request)
    tool_call_id = _tool_call_id(request)
    started_at = time.monotonic()
    budget = current_execution_budget()
    if budget is not None:
        budget.charge(tool or "unknown", (risk_by_name or {}).get(tool, ToolRisk.READ_ONLY))

    record_tool_started(
        source,
        tool=tool,
        tool_call_id=tool_call_id,
        args=_tool_call_args(request),
    )

    try:
        if budget is None:
            result = execute(request)
        else:
            with budget.tool_slot():
                result = execute(request)
    except ToolBudgetExceeded as exc:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        record_tool_failed(
            source,
            tool=tool,
            tool_call_id=tool_call_id,
            error=exc,
            duration_ms=duration_ms,
        )
        raise
    except Exception as exc:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        record_tool_failed(
            source,
            tool=tool,
            tool_call_id=tool_call_id,
            error=exc,
            duration_ms=duration_ms,
        )
        return _tool_error_message(request, exc)

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


def _hook_context_from_request(request, point: HookPoint, payload: dict[str, Any]) -> HookContext:
    # LangGraph's ToolRuntime is a TypedDict. In Python 3.12 getattr
    # does not read TypedDict keys, so fall back to dict access.
    runtime = getattr(request.runtime, "context", None)
    if runtime is None and isinstance(request.runtime, dict):
        runtime = request.runtime.get("context")
    return HookContext(
        point=point,
        subject=_tool_call_name(request) or "unknown",
        workspace_id=str(getattr(runtime, "workspace_id", "")) if runtime is not None else "",
        session_id=str(getattr(runtime, "session_id", "")) if runtime is not None else "",
        execution_id=str(getattr(runtime, "execution_id", "") or "") if runtime is not None else "",
        run_id=str(getattr(runtime, "run_id", "") or "") if runtime is not None else "",
        workspace_root=str(getattr(runtime, "workspace_root", "") or "") if runtime is not None else "",
        payload=payload,
    )


class ObservedToolNode(ToolNode):
    """ToolNode with centralized tool boundary hook events."""

    def __init__(
        self,
        tools: Sequence[BaseTool | Callable],
        *,
        event_source: str = "agent_tool_node",
        risk_by_name: dict[str, ToolRisk] | None = None,
        pipeline=None,
        hook_dispatcher=None,
        **kwargs,
    ) -> None:
        existing_wrapper = kwargs.pop("wrap_tool_call", None)
        hooks = hook_dispatcher or NOOP_HOOK_DISPATCHER

        def observed_wrapper(request, execute):
            """Compose centralized observation with an optional existing wrapper."""
            if pipeline is not None:
                return pipeline.invoke(request, execute)
            hook_context, hook_decision = hooks.dispatch(_hook_context_from_request(
                request,
                HookPoint.PRE_TOOL_USE,
                {"args": _tool_call_args(request) or {}},
            ))
            if hook_decision.action in {HookAction.REJECT, HookAction.DENY}:
                return _tool_denied_message(request, hook_decision.reason or "PreToolUse hook rejected the call.")
            replacement = hook_context.payload.get("args", _tool_call_args(request) or {})
            if replacement != (_tool_call_args(request) or {}):
                if not isinstance(replacement, dict):
                    return _tool_denied_message(request, "PreToolUse hook must replace args with an object.")
                request = request.override(tool_call={**request.tool_call, "args": replacement})
            if existing_wrapper is None:
                result = _observe_tool_call(event_source, request, execute, risk_by_name)
            else:
                def wrapped_execute(observed_request):
                    """Preserve a caller-provided wrapper inside observation hooks."""
                    return existing_wrapper(observed_request, execute)

                result = _observe_tool_call(event_source, request, wrapped_execute, risk_by_name)
            hooks.dispatch(_hook_context_from_request(
                request,
                HookPoint.POST_TOOL_USE,
                {"status": "error" if _result_is_error(result) else "success", "content": _result_preview(result)},
            ))
            return result

        super().__init__(tools, wrap_tool_call=observed_wrapper, **kwargs)