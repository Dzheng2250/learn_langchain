"""Central ToolNode wrapper that observes every tool-call boundary."""

import time
import posixpath
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables.config import get_config_list, get_executor_for_config
from langchain_core.tools import BaseTool
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt.tool_node import ToolRuntime

from src.core.common.content import message_content_text
from src.core.common.debug import debug_print

from src.core.hooks import HookAction, HookContext, HookPoint, NOOP_HOOK_DISPATCHER
from src.core.telemetry import record_tool_failed, record_tool_finished, record_tool_started
from src.core.agent.budget import ToolBudgetExceeded, current_execution_budget
from src.core.resource_activity import (
    bind_resource_activity, current_resource_context, lookup_resource_evidence,
)
from src.core.state.tool_ledger import ToolRecoveryRequired
from src.core.tools.catalog import ToolEffect, ToolRisk, ToolSpec


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

    try:
        record_tool_started(
            source,
            tool=tool,
            tool_call_id=tool_call_id,
            args=_tool_call_args(request),
        )
    except Exception as telemetry_exc:
        debug_print("TOOL START TELEMETRY FAILED", telemetry_exc)

    try:
        if budget is None:
            result = execute(request)
        else:
            with budget.tool_slot():
                result = execute(request)
    except ToolBudgetExceeded as exc:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        try:
            record_tool_failed(
                source,
                tool=tool,
                tool_call_id=tool_call_id,
                error=exc,
                duration_ms=duration_ms,
            )
        except Exception as telemetry_exc:
            debug_print("TOOL FAILURE TELEMETRY FAILED", telemetry_exc)
        raise
    except Exception as exc:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        try:
            record_tool_failed(
                source,
                tool=tool,
                tool_call_id=tool_call_id,
                error=exc,
                duration_ms=duration_ms,
            )
        except Exception as telemetry_exc:
            debug_print("TOOL FAILURE TELEMETRY FAILED", telemetry_exc)
        return _tool_error_message(request, exc)

    duration_ms = int((time.monotonic() - started_at) * 1000)
    preview = _result_preview(result)
    try:
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
    except Exception as telemetry_exc:
        debug_print("TOOL RESULT TELEMETRY FAILED", telemetry_exc)
    return result

def _hook_context_from_request(request, point: HookPoint, payload: dict[str, Any]) -> HookContext:
    runtime = getattr(request.runtime, "context", None)
    return HookContext(
        point=point,
        subject=_tool_call_name(request) or "unknown",
        workspace_id=str(getattr(runtime, "workspace_id", "")),
        session_id=str(getattr(runtime, "session_id", "")),
        execution_id=str(getattr(runtime, "execution_id", "") or ""),
        run_id=str(getattr(runtime, "run_id", "") or ""),
        workspace_root=str(getattr(runtime, "workspace_root", "") or ""),
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
        resource_activity_recorder=None,
        actor: str | None = None,
        **kwargs,
    ) -> None:
        existing_wrapper = kwargs.pop("wrap_tool_call", None)
        hooks = hook_dispatcher or NOOP_HOOK_DISPATCHER

        def observed_wrapper(request, execute):
            """Compose centralized observation with an optional existing wrapper."""
            if pipeline is not None:
                return pipeline.invoke(request, execute)
            runtime = getattr(request.runtime, "context", None)
            outer_context = current_resource_context()
            evidence_context = SimpleNamespace(
                args=_tool_call_args(request) or {},
                execution_id=getattr(runtime, "execution_id", None) or getattr(outer_context, "execution_id", None),
                workspace_root=getattr(runtime, "workspace_root", "") or getattr(outer_context, "workspace_root", ""),
                tool_name=_tool_call_name(request) or "unknown",
            )
            hook_context, hook_decision = hooks.dispatch(_hook_context_from_request(
                request,
                HookPoint.PRE_TOOL_USE,
                {
                    "args": _tool_call_args(request) or {},
                    "resource_evidence": lookup_resource_evidence(
                        resource_activity_recorder, evidence_context, source="observed_tool_node"
                    ),
                },
            ))
            if hook_decision.action in {HookAction.REJECT, HookAction.DENY}:
                return _tool_denied_message(request, hook_decision.reason or "PreToolUse hook rejected the call.")
            replacement = hook_context.payload.get("args", _tool_call_args(request) or {})
            if replacement != (_tool_call_args(request) or {}):
                if not isinstance(replacement, dict):
                    return _tool_denied_message(request, "PreToolUse hook must replace args with an object.")
                request = request.override(tool_call={**request.tool_call, "args": replacement})
            if existing_wrapper is None:
                if resource_activity_recorder is not None and outer_context is not None:
                    nested_context = replace(
                        outer_context,
                        tool_name=_tool_call_name(request) or "unknown",
                        tool_call_id=_tool_call_id(request) or "",
                        actor=actor or getattr(outer_context, "actor", "parent") or "parent",
                    )
                    with bind_resource_activity(resource_activity_recorder, nested_context) as activity_ids:
                        result = _observe_tool_call(event_source, request, execute, risk_by_name)
                else:
                    activity_ids = []
                    result = _observe_tool_call(event_source, request, execute, risk_by_name)
            else:
                activity_ids = []
                def wrapped_execute(observed_request):
                    """Preserve a caller-provided wrapper inside observation hooks."""
                    return existing_wrapper(observed_request, execute)

                result = _observe_tool_call(event_source, request, wrapped_execute, risk_by_name)
            try:
                hooks.dispatch(_hook_context_from_request(
                    request,
                    HookPoint.POST_TOOL_USE,
                    {
                        "status": "error" if _result_is_error(result) else "success",
                        "content": _result_preview(result),
                        "resource_activity_ids": activity_ids,
                    },
                ))
            except Exception as hook_exc:
                debug_print("POST TOOL HOOK FAILED", hook_exc)
            return result

        def fault_contained_wrapper(request, execute):
            """Keep fallback ToolNode failures model-visible and graph-local."""
            try:
                return observed_wrapper(request, execute)
            except (GraphBubbleUp, ToolBudgetExceeded, ToolRecoveryRequired):
                raise
            except Exception as exc:
                try:
                    record_tool_failed(
                        event_source,
                        tool=_tool_call_name(request),
                        tool_call_id=_tool_call_id(request),
                        error=exc,
                    )
                except Exception as telemetry_exc:
                    debug_print("TOOL FAILURE TELEMETRY FAILED", telemetry_exc)
                return _tool_error_message(request, exc)

        super().__init__(tools, wrap_tool_call=fault_contained_wrapper, **kwargs)


class LedgerBackedToolNode(ObservedToolNode):
    """Execute a complete tool batch with per-call durable recovery.

    LangGraph checkpoints the batch only after this node returns. Each call is
    therefore committed to the Tool Ledger first; if an interrupt restarts the
    node, completed calls replay their exact ToolMessage and only pending calls
    execute. Side effects remain sequential while explicitly safe reads may run
    in parallel.
    """

    def __init__(self, tools, *, specs: dict[str, ToolSpec], **kwargs) -> None:
        self.execution_specs = dict(specs)
        kwargs.setdefault(
            "risk_by_name",
            {name: spec.risk for name, spec in self.execution_specs.items()},
        )
        super().__init__(tools, **kwargs)

    def _func(self, input, config, runtime):
        tool_calls, input_type = self._parse_input(input)
        completed = completed_tool_call_ids(input)
        pending = [call for call in tool_calls if str(call.get("id") or "") not in completed]
        if not pending:
            return self._combine_tool_outputs([], input_type)

        conflicts = conflicting_mutation_calls(pending, self.execution_specs)

        config_list = get_config_list(config, len(pending))
        calls_with_runtime = []
        for call, cfg in zip(pending, config_list, strict=False):
            state = self._extract_state(input, cfg)
            calls_with_runtime.append((
                call,
                ToolRuntime(
                    state=state,
                    tool_call_id=call["id"],
                    config=cfg,
                    context=runtime.context,
                    store=runtime.store,
                    stream_writer=runtime.stream_writer,
                    tools=list(self.tools_by_name.values()),
                    execution_info=runtime.execution_info,
                    server_info=runtime.server_info,
                ),
            ))

        outputs = []
        index = 0
        while index < len(calls_with_runtime):
            call, tool_runtime = calls_with_runtime[index]
            call_id = str(call.get("id") or "")
            if call_id in conflicts:
                outputs.append(ToolMessage(
                    content=(
                        "Tool call was rejected before execution because another "
                        "mutation in the same assistant response targets the same "
                        "Workspace path. Combine the edits into one "
                        "apply_workspace_patch call, or wait for the first result."
                    ),
                    name=str(call.get("name") or "unknown"),
                    tool_call_id=call_id,
                    status="error",
                    additional_kwargs={"tool_execution_status": "resource_conflict"},
                ))
                index += 1
                continue
            spec = self.execution_specs.get(str(call.get("name") or ""))
            if spec is None or not spec.parallel_safe:
                outputs.append(self._run_one(call, input_type, tool_runtime))
                index += 1
                continue

            budget = current_execution_budget()
            wave_capacity = len(calls_with_runtime) - index
            if budget is not None:
                remaining = budget.remaining_for(spec.risk)
                # A single call must still enter the pipeline at zero capacity:
                # completed ledger results replay before budget enforcement.
                wave_capacity = max(
                    1,
                    min(budget.max_parallel_tool_calls, remaining),
                )
            wave = []
            while index < len(calls_with_runtime) and len(wave) < wave_capacity:
                candidate, candidate_runtime = calls_with_runtime[index]
                candidate_spec = self.execution_specs.get(
                    str(candidate.get("name") or "")
                )
                if (
                    candidate_spec is None
                    or not candidate_spec.parallel_safe
                    or candidate_spec.risk != spec.risk
                ):
                    break
                wave.append((candidate, candidate_runtime))
                index += 1
            with get_executor_for_config(config) as executor:
                outputs.extend(executor.map(
                    self._run_one,
                    [item[0] for item in wave],
                    [input_type] * len(wave),
                    [item[1] for item in wave],
                ))

        return self._combine_tool_outputs(outputs, input_type)


def conflicting_mutation_calls(tool_calls, specs: dict[str, ToolSpec]) -> set[str]:
    """Return every call in a same-resource mutation conflict group."""
    calls_by_path: dict[str, list[str]] = {}
    for call in tool_calls:
        call_id = str(call.get("id") or "")
        spec = specs.get(str(call.get("name") or ""))
        if spec is None or spec.effect != ToolEffect.WORKSPACE_MUTATION:
            continue
        args = dict(call.get("args") or {})
        try:
            paths = tuple(spec.resource_resolver(args)) if spec.resource_resolver else ()
        except (OSError, ValueError):
            # The ordinary pipeline returns the detailed parser/validation error.
            paths = ()
        if not paths:
            paths = tuple(
                value
                for key in ("path", "source", "destination")
                if isinstance((value := args.get(key)), str) and value.strip()
            )
        for path in paths:
            normalized = posixpath.normpath(path.replace("\\", "/")).casefold()
            calls_by_path.setdefault(normalized, []).append(call_id)
    return {
        call_id
        for call_ids in calls_by_path.values()
        if len(call_ids) > 1
        for call_id in call_ids
    }

def completed_tool_call_ids(input) -> set[str]:
    """Return tool IDs completed after the latest assistant request."""
    if isinstance(input, list):
        messages = input
    elif isinstance(input, dict):
        messages = list(input.get("messages") or [])
    else:
        messages = list(getattr(input, "messages", []) or [])
    assistant_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if isinstance(messages[index], AIMessage)
            and getattr(messages[index], "tool_calls", None)
        ),
        None,
    )
    if assistant_index is None:
        return set()
    return {
        str(message.tool_call_id or "")
        for message in messages[assistant_index + 1 :]
        if isinstance(message, ToolMessage)
    }
