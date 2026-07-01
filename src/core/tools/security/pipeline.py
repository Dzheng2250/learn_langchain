"""Single execution boundary for hooks, policy, approval, and observation."""

import time
from pathlib import PurePosixPath

from langchain_core.messages import ToolMessage
from langgraph.types import interrupt

from src.core.agent.budget import ToolBudgetExceeded, current_execution_budget
from src.core.common.content import message_content_text
from src.core.hooks import (
    HookAction, HookContext, HookDecision, HookPoint, NOOP_HOOK_DISPATCHER,
)
from src.core.telemetry import record_tool_failed, record_tool_finished, record_tool_started
from src.core.tools.catalog import ToolCapability
from src.core.tools.security.command_rules import command_rule_key
from src.core.tools.security.enforcement import CapabilityEnforcer
from src.core.tools.security.models import PolicyAction, ToolCallContext


class ToolExecutionPipeline:
    """Authorize and execute every LangGraph tool request consistently."""

    def __init__(self, specs, *, policy, approvals, hook_dispatcher=None, enforcer=None, event_source="agent_tool_node"):
        self.specs = dict(specs)
        self.policy = policy
        self.approvals = approvals
        self.hook_dispatcher = hook_dispatcher or NOOP_HOOK_DISPATCHER
        self.enforcer = enforcer or CapabilityEnforcer()
        self.event_source = event_source

    def invoke(self, request, execute):
        context = self._context_from_request(request)
        hook_context, hook_decision = self.hook_dispatcher.dispatch(
            self._hook_context(context, HookPoint.PRE_TOOL_USE, {"args": context.args})
        )
        if hook_decision.action in {HookAction.REJECT, HookAction.DENY}:
            return self._denied(context, hook_decision.reason)
        replacement = hook_context.payload.get("args", context.args)
        if replacement != context.args:
            if not isinstance(replacement, dict):
                return self._denied(context, "PreToolUse hook must replace args with an object.")
            context = context.with_args(replacement)
        request = self._validated_request(request, context)
        rule_key, persistable = self._rule_identity(context)
        decision = self.policy.evaluate(
            context, rule_key=rule_key, persistable=persistable
        )
        if decision.action == PolicyAction.DENY:
            return self._denied(context, decision.reason)
        if decision.action == PolicyAction.ASK:
            identity_error = self._approval_identity_error(context)
            if identity_error:
                return self._denied(context, identity_error)
            _permission_context, hook_decision = self.hook_dispatcher.dispatch(
                self._hook_context(
                    context,
                    HookPoint.PERMISSION_REQUEST,
                    {"args": context.args, "reason": decision.reason,
                     "capabilities": [item.value for item in decision.capabilities]},
                )
            )
            if hook_decision.action == HookAction.ALLOW_ONCE:
                allowed = self.approvals.allow_once_from_hook(context, decision)
            elif hook_decision.action in {HookAction.DENY, HookAction.REJECT}:
                allowed = False
            else:
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
            self._dispatch_post_tool(context, "error", error_type=type(exc).__name__)
            record_tool_failed(
                self.event_source,
                tool=context.tool_name,
                tool_call_id=context.tool_call_id,
                error=exc,
                duration_ms=int((time.monotonic() - started_at) * 1000),
            )
            return self._error(context, exc)
        preview = message_content_text(value) or repr(value)
        self._dispatch_post_tool(context, "success", content=preview)
        record_tool_finished(
            self.event_source,
            tool=context.tool_name,
            tool_call_id=context.tool_call_id,
            content=preview,
            duration_ms=int((time.monotonic() - started_at) * 1000),
        )
        return value

    def _dispatch_post_tool(self, context, status: str, **payload) -> HookDecision:
        _updated, decision = self.hook_dispatcher.dispatch(
            self._hook_context(
                context,
                HookPoint.POST_TOOL_USE,
                {"status": status, **payload},
            )
        )
        return decision

    @staticmethod
    def _hook_context(context, point, payload):
        return HookContext(
            point=point,
            subject=context.tool_name,
            workspace_id=context.workspace_id,
            session_id=context.session_id,
            execution_id=context.execution_id or "",
            run_id=context.run_id or "",
            workspace_root=context.workspace_root,
            payload=payload,
        )
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
    def _approval_identity_error(context) -> str | None:
        missing = [
            name
            for name, value in (
                ("workspace_id", context.workspace_id),
                ("session_id", context.session_id),
                ("execution_id", context.execution_id),
                ("tool_call_id", context.tool_call_id),
            )
            if not value
        ]
        if not missing:
            return None
        return "Approval context is missing required identity: " + ", ".join(missing)

    @staticmethod
    def _rule_identity(context):
        if ToolCapability.COMMAND_EXECUTION in context.spec.capabilities:
            return command_rule_key(str(context.args.get("command", "")))
        if ToolCapability.FILE_WRITE in context.spec.capabilities:
            path = (
                context.args.get("path")
                or context.args.get("source")
                or context.args.get("change_set_id")
                or ""
            )
            normalized = str(path).replace("\\", "/").strip("/")
            if not normalized:
                return f"workspace-write:{context.tool_name}:", False
            scope = PurePosixPath(normalized).parent.as_posix()
            return f"workspace-write:{context.tool_name}:{scope}", True
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
