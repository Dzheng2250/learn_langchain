"""Single execution boundary for hooks, policy, approval, and observation."""

import time
import posixpath
from pathlib import PurePosixPath

from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.types import interrupt
from pydantic import ValidationError

from src.core.agent.budget import ToolBudgetExceeded, current_execution_budget
from src.core.common.content import message_content_text
from src.core.common.debug import debug_print
from src.core.hooks import (
    HookAction, HookContext, HookDecision, HookPoint, NOOP_HOOK_DISPATCHER,
)
from src.core.telemetry import record_tool_failed, record_tool_finished, record_tool_started
from src.core.tools.catalog import ToolCapability
from src.core.tools.security.command_rules import command_rule_key
from src.core.tools.security.enforcement import CapabilityEnforcer
from src.core.resource_activity import bind_resource_activity, lookup_resource_evidence
from src.core.state.tool_ledger import ToolRecoveryRequired
from src.core.tools.errors import ToolSideEffectUncertain
from src.core.tools.security.models import PolicyAction, ToolCallContext


class ToolExecutionPipeline:
    """Authorize and execute every LangGraph tool request consistently."""

    def __init__(self, specs, *, policy, approvals, approval_coordinator=None, hook_dispatcher=None, enforcer=None, event_source="agent_tool_node", resource_activity_recorder=None, tool_ledger=None):
        self.specs = dict(specs)
        self.policy = policy
        self.approvals = approvals
        self.approval_coordinator = approval_coordinator
        self.hook_dispatcher = hook_dispatcher or NOOP_HOOK_DISPATCHER
        self.enforcer = enforcer or CapabilityEnforcer()
        self.event_source = event_source
        self.resource_activity_recorder = resource_activity_recorder
        self.tool_ledger = tool_ledger

    def invoke(self, request, execute):
        """Run one tool call without leaking ordinary failures into the graph."""
        context = None
        try:
            context = self._context_from_request(request)
            return self._invoke_with_context(request, execute, context)
        except (GraphBubbleUp, ToolBudgetExceeded, ToolRecoveryRequired):
            # These are state-machine control signals, not tool failures.
            raise
        except Exception as exc:
            return self._contain_failure(request, context, exc)

    def _invoke_with_context(self, request, execute, context):
        try:
            evidence_context = self._resolve_resources(context)
        except (OSError, ValueError):
            evidence_context = context
        pre_payload = {
            "args": context.args,
            "resource_evidence": lookup_resource_evidence(
                self.resource_activity_recorder,
                evidence_context,
                source="tool_pipeline",
            ),
        }
        hook_context, hook_decision = self.hook_dispatcher.dispatch(
            self._hook_context(context, HookPoint.PRE_TOOL_USE, pre_payload)
        )
        if hook_decision.action in {HookAction.REJECT, HookAction.DENY}:
            return self._denied(context, hook_decision.reason)
        replacement = hook_context.payload.get("args", context.args)
        if replacement != context.args:
            if not isinstance(replacement, dict):
                return self._denied(context, "PreToolUse hook must replace args with an object.")
            context = context.with_args(replacement)
        try:
            request = self._validated_request(request, context)
            context = context.with_args(dict(request.tool_call.get("args") or {}))
            context = self._resolve_resources(context)
        except (ValidationError, OSError, ValueError) as exc:
            self._dispatch_post_tool(
                context,
                "error",
                error_type=type(exc).__name__,
                resource_activity_ids=[],
            )
            record_tool_failed(
                self.event_source,
                tool=context.tool_name,
                tool_call_id=context.tool_call_id,
                error=exc,
            )
            return self._error(context, exc)
        if self.tool_ledger is not None:
            completed = self.tool_ledger.replay_completed(context)
            if completed.action == "replay" and completed.message is not None:
                return completed.message
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
                try:
                    self.enforcer.validate(context)
                except (OSError, ValueError, PermissionError) as exc:
                    return self._denied(context, str(exc))
                if self.approval_coordinator is None:
                    allowed = self.approvals.allow_once_from_hook(context, decision)
                else:
                    allowed = self.approval_coordinator.allow_once_from_hook(
                        context, decision
                    )
            elif hook_decision.action in {HookAction.DENY, HookAction.REJECT}:
                return self._denied(
                    context,
                    hook_decision.reason or "PermissionRequest hook denied the call.",
                )
            else:
                try:
                    self.enforcer.validate(context)
                except (OSError, ValueError, PermissionError) as exc:
                    return self._denied(context, str(exc))
                if self.approval_coordinator is None:
                    pending = self.approvals.request(context, decision)
                    if pending.get("status") == "resolved":
                        allowed = str(pending.get("response", "")).startswith("allow_")
                    else:
                        response = interrupt({"type": "tool_approval_required", "request": pending})
                        allowed = self.approvals.resolve_interrupt(
                            context, decision, pending["request_id"], response
                        )
                else:
                    flow = self.approval_coordinator.begin(context, decision)
                    if flow.allowed is None:
                        response = interrupt(
                            {"type": "tool_approval_required", "request": flow.request}
                        )
                        allowed = self.approval_coordinator.resolve_manual(
                            context, decision, flow.request, response
                        )
                    else:
                        allowed = flow.allowed
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
        else:
            try:
                self.enforcer.validate(context)
            except (OSError, ValueError, PermissionError) as exc:
                return self._denied(context, str(exc))
        return self._execute(context, request, execute)

    def _contain_failure(self, request, context, exc: Exception) -> ToolMessage:
        """Convert any ordinary pipeline-stage failure into a tool result."""
        tool_name, tool_call_id = self._request_identity(request, context)
        if context is not None:
            try:
                self._dispatch_post_tool(
                    context,
                    "error",
                    error_type=type(exc).__name__,
                    resource_activity_ids=[],
                )
            except Exception as hook_exc:
                debug_print("TOOL ERROR HOOK FAILED", hook_exc)
        try:
            record_tool_failed(
                self.event_source,
                tool=tool_name,
                tool_call_id=tool_call_id,
                error=exc,
            )
        except Exception as telemetry_exc:
            debug_print("TOOL FAILURE TELEMETRY FAILED", telemetry_exc)
        return ToolMessage(
            content=f"Tool {tool_name} failed: {type(exc).__name__}: {exc}",
            name=tool_name,
            tool_call_id=tool_call_id,
            status="error",
            additional_kwargs={"tool_execution_status": "failed"},
        )

    @staticmethod
    def _request_identity(request, context=None) -> tuple[str, str]:
        """Read a best-effort identity even when request decoding failed."""
        if context is not None:
            return context.tool_name, context.tool_call_id or "unknown-tool-call"
        try:
            call = request.tool_call
            if not isinstance(call, dict):
                call = {}
        except Exception:
            call = {}
        return (
            str(call.get("name") or "unknown"),
            str(call.get("id") or "unknown-tool-call"),
        )

    def _execute(self, context, request, execute):
        started_at = time.monotonic()
        budget = current_execution_budget()
        if budget is not None:
            # Keep budget pauses outside the durable execution claim. The
            # checkpointed node performs the same wave-level check, while this
            # guard also protects fallback/direct pipeline callers.
            budget.require_capacity(
                context.tool_name,
                context.spec.risk,
                tool_call_id=context.tool_call_id,
            )
        if self.tool_ledger is not None:
            claim = self.tool_ledger.claim(context)
            if claim.action == "replay" and claim.message is not None:
                return claim.message
        if budget is not None:
            budget.charge(context.tool_name, context.spec.risk)
        try:
            record_tool_started(
                self.event_source,
                tool=context.tool_name,
                tool_call_id=context.tool_call_id,
                args=context.args,
            )
        except Exception as telemetry_exc:
            debug_print("TOOL START TELEMETRY FAILED", telemetry_exc)
        activity_ids = []
        try:
            with bind_resource_activity(self.resource_activity_recorder, context) as activity_ids:
                if budget is None:
                    value = execute(request)
                else:
                    with budget.tool_slot():
                        value = execute(request)
        except ToolBudgetExceeded as exc:
            self._record_failure(context, exc, started_at)
            raise
        except ToolSideEffectUncertain as exc:
            self._record_failure(context, exc, started_at)
            raise ToolRecoveryRequired(
                str(exc),
                tool_call_id=context.tool_call_id,
                tool_name=context.tool_name,
            ) from exc
        except Exception as exc:
            self._dispatch_post_tool(
                context,
                "error",
                error_type=type(exc).__name__,
                resource_activity_ids=activity_ids,
            )
            self._record_failure(context, exc, started_at)
            message = self._error(context, exc)
            if self.tool_ledger is not None:
                self.tool_ledger.finish(context, message)
            return message
        if self.tool_ledger is not None:
            self.tool_ledger.finish(context, value)
        preview = message_content_text(value) or repr(value)
        self._dispatch_post_tool(
            context,
            "success",
            content=preview,
            resource_activity_ids=activity_ids,
        )
        try:
            record_tool_finished(
                self.event_source,
                tool=context.tool_name,
                tool_call_id=context.tool_call_id,
                content=preview,
                duration_ms=int((time.monotonic() - started_at) * 1000),
            )
        except Exception as telemetry_exc:
            debug_print("TOOL SUCCESS TELEMETRY FAILED", telemetry_exc)
        return value

    def _record_failure(self, context, exc: Exception, started_at=None) -> None:
        try:
            record_tool_failed(
                self.event_source,
                tool=context.tool_name,
                tool_call_id=context.tool_call_id,
                error=exc,
                duration_ms=(
                    int((time.monotonic() - started_at) * 1000)
                    if started_at is not None
                    else None
                ),
            )
        except Exception as telemetry_exc:
            debug_print("TOOL FAILURE TELEMETRY FAILED", telemetry_exc)

    def _dispatch_post_tool(self, context, status: str, **payload) -> HookDecision:
        try:
            _updated, decision = self.hook_dispatcher.dispatch(
                self._hook_context(
                    context,
                    HookPoint.POST_TOOL_USE,
                    {"status": status, **payload},
                )
            )
            return decision
        except Exception as hook_exc:
            debug_print("POST TOOL HOOK FAILED", hook_exc)
            return HookDecision()

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
            getattr(runtime, "turn_index", None),
            getattr(runtime, "slice_id", None),
        )

    @staticmethod
    def _validated_request(request, context):
        # Validate only arguments exposed to the model. ``args_schema`` also
        # contains LangGraph-injected parameters such as ToolRuntime, which are
        # deliberately absent from the LLM tool call and injected by ToolNode.
        schema = getattr(request.tool, "tool_call_schema", None)
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
    def _resolve_resources(context):
        resolver = context.spec.resource_resolver
        if resolver is None:
            return context
        resolved = resolver(context.args)
        if resolved is None:
            raise ValueError("Tool resource resolver returned no paths.")
        paths = tuple(resolved)
        if not paths:
            raise ValueError("Tool resource resolver returned no paths.")
        return context.with_resource_paths(paths)

    @staticmethod
    def _rule_identity(context):
        if ToolCapability.COMMAND_EXECUTION in context.spec.capabilities:
            return command_rule_key(str(context.args.get("command", "")))
        if ToolCapability.FILE_WRITE in context.spec.capabilities:
            if context.resource_paths:
                parents = [
                    PurePosixPath(path.replace("\\", "/").strip("/")).parent.as_posix()
                    for path in context.resource_paths
                ]
                try:
                    scope = posixpath.commonpath(parents)
                except ValueError:
                    return f"workspace-write:{context.tool_name}:", False
                scope = scope or "."
                return f"workspace-write:{context.tool_name}:{scope}", True
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
