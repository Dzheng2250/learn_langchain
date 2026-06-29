"""Single execution boundary for hooks, policy, approval, and observation."""

import time

from langchain_core.messages import ToolMessage
from langgraph.types import interrupt

from src.core.agent.budget import ToolBudgetExceeded, current_execution_budget
from src.core.common.content import message_content_text
from src.core.telemetry import record_tool_failed, record_tool_finished, record_tool_started
from src.core.tools.catalog import ToolCapability
from src.core.tools.security.command_rules import command_rule_key
from src.core.tools.security.hooks import HookRunner
from src.core.tools.security.enforcement import CapabilityEnforcer
from src.core.tools.security.models import (
    HookAction, PolicyAction, ToolCallContext,
    ToolExecutionResult, ToolExecutionStatus,
)


class ToolExecutionPipeline:
    """Authorize and execute every LangGraph tool request consistently."""

    def __init__(self, specs, *, policy, approvals, hooks=None, enforcer=None, event_source="agent_tool_node"):
        self.specs = dict(specs)
        self.policy = policy
        self.approvals = approvals
        self.hooks = hooks or HookRunner()
        self.enforcer = enforcer or CapabilityEnforcer()
        self.event_source = event_source

    def invoke(self, request, execute):
        context = self._context_from_request(request)
        context, hook_decision = self.hooks.before(context)
        if hook_decision.action == HookAction.REJECT:
            return self._denied(context, hook_decision.reason)
        request = self._validated_request(request, context)
        rule_key, persistable = self._rule_identity(context)
        decision = self.policy.evaluate(
            context, rule_key=rule_key, persistable=persistable
        )
        if decision.action == PolicyAction.DENY:
            return self._denied(context, decision.reason)
        if decision.action == PolicyAction.ASK:
            pending = self.approvals.request(context, decision)
            if pending.get("status") == "resolved":
                allowed = str(pending.get("response", "")).startswith("allow_")
            else:
                response = interrupt({"type": "tool_approval_required", "request": pending})
                allowed = self.approvals.resolve_interrupt(
                    context, decision, pending["request_id"], response
                )
            if not allowed:
                return self._denied(context, "User denied the tool call.")
            refreshed = self.policy.evaluate(
                context, rule_key=rule_key, persistable=persistable
            )
            if refreshed.action == PolicyAction.DENY:
                return self._denied(context, refreshed.reason)
        try:
            self.enforcer.validate(context)
        except (OSError, ValueError, PermissionError) as exc:
            return self._denied(context, str(exc))
        return self._execute(context, request, execute)

    def _execute(self, context, request, execute):
        started_at = time.monotonic()
        budget = current_execution_budget()
        if budget is not None:
            budget.charge(context.tool_name, context.spec.risk)
        record_tool_started(
            self.event_source,
            tool=context.tool_name,
            tool_call_id=context.tool_call_id,
            args=context.args,
        )
        try:
            if budget is None:
                value = execute(request)
            else:
                with budget.tool_slot():
                    value = execute(request)
        except ToolBudgetExceeded as exc:
            record_tool_failed(
                self.event_source,
                tool=context.tool_name,
                tool_call_id=context.tool_call_id,
                error=exc,
                duration_ms=int((time.monotonic() - started_at) * 1000),
            )
            raise
        except Exception as exc:
            self.hooks.error(context, exc)
            record_tool_failed(
                self.event_source,
                tool=context.tool_name,
                tool_call_id=context.tool_call_id,
                error=exc,
                duration_ms=int((time.monotonic() - started_at) * 1000),
            )
            return self._error(context, exc)
        preview = message_content_text(value) or repr(value)
        if _contains_tool_error(value):
            result = ToolExecutionResult(
                ToolExecutionStatus.ERROR,
                value=value,
                message=preview,
            )
            self.hooks.after(context, result)
            record_tool_failed(
                self.event_source,
                tool=context.tool_name,
                tool_call_id=context.tool_call_id,
                message="Tool returned an error result.",
                payload={"content_preview": preview},
                duration_ms=int((time.monotonic() - started_at) * 1000),
            )
            return value
        result = ToolExecutionResult(ToolExecutionStatus.SUCCESS, value=value)
        self.hooks.after(context, result)
        record_tool_finished(
            self.event_source,
            tool=context.tool_name,
            tool_call_id=context.tool_call_id,
            content=preview,
            duration_ms=int((time.monotonic() - started_at) * 1000),
        )
        return value
    def _context_from_request(self, request):
        call = request.tool_call
        name = call.get("name") or "unknown"
        spec = self.specs.get(name)
        if spec is None:
            raise PermissionError(f"Unregistered tool: {name}")
        runtime = getattr(request.runtime, "context", None)
        return ToolCallContext(
            name,
            call.get("id") or "",
            dict(call.get("args") or {}),
            str(getattr(runtime, "workspace_id", "")),
            str(getattr(runtime, "session_id", "")),
            getattr(runtime, "execution_id", None),
            getattr(runtime, "run_id", None),
            getattr(runtime, "actor", "parent"),
            spec,
            getattr(runtime, "workspace_root", ""),
        )

    @staticmethod
    def _validated_request(request, context):
        schema = getattr(request.tool, "args_schema", None)
        args = context.args
        if schema is not None:
            args = schema.model_validate(args).model_dump()
        return request.override(tool_call={**request.tool_call, "args": args})

    @staticmethod
    def _rule_identity(context):
        if ToolCapability.COMMAND_EXECUTION in context.spec.capabilities:
            return command_rule_key(str(context.args.get("command", "")))
        return f"tool:{context.tool_name}", True

    @staticmethod
    def _error(context, exc):
        return ToolMessage(
            content=f"Tool {context.tool_name} failed: {type(exc).__name__}: {exc}",
            name=context.tool_name,
            tool_call_id=context.tool_call_id,
            status="error",
        )

    @staticmethod
    def _denied(context, reason):
        return ToolMessage(
            content=f"Tool {context.tool_name} was denied: {reason}",
            name=context.tool_name,
            tool_call_id=context.tool_call_id,
            status="error",
            additional_kwargs={"tool_execution_status": "denied"},
        )


def _contains_tool_error(value) -> bool:
    """Return whether a tool result, including batched results, reports failure."""
    if isinstance(value, (list, tuple)):
        return any(_contains_tool_error(item) for item in value)
    return getattr(value, "status", None) == "error"
