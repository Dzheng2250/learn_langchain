"""Trusted in-process hooks around the tool authorization boundary."""

import time
from typing import Protocol

from src.core.tools.security.models import HookAction, HookDecision, ToolCallContext


class ToolHook(Protocol):
    def before_tool(self, context: ToolCallContext) -> HookDecision: ...
    def after_tool(self, context: ToolCallContext, result) -> None: ...
    def on_tool_error(self, context: ToolCallContext, error: Exception) -> None: ...


class HookRunner:
    """Compose hooks; hooks may restrict but never grant permission."""

    def __init__(self, hooks=(), *, timeout_seconds: float = 2.0) -> None:
        self.hooks = tuple(hooks)
        self.timeout_seconds = max(0.01, float(timeout_seconds))

    def before(self, context):
        current = context
        for hook in self.hooks:
            started = time.monotonic()
            try:
                decision = hook.before_tool(current)
            except Exception as exc:
                return current, HookDecision(
                    HookAction.REJECT,
                    reason=f"Pre-tool hook failed: {type(exc).__name__}",
                )
            if time.monotonic() - started > self.timeout_seconds:
                return current, HookDecision(
                    HookAction.REJECT,
                    reason="Pre-tool hook exceeded its execution deadline.",
                )
            if decision.action == HookAction.REJECT:
                return current, decision
            if decision.action == HookAction.REPLACE_ARGS:
                if decision.args is None:
                    return current, HookDecision(
                        HookAction.REJECT,
                        reason="Hook omitted replacement arguments.",
                    )
                current = current.with_args(decision.args)
        return current, HookDecision()

    def after(self, context, result) -> None:
        for hook in self.hooks:
            try:
                hook.after_tool(context, result)
            except Exception:
                continue

    def error(self, context, error) -> None:
        for hook in self.hooks:
            try:
                hook.on_tool_error(context, error)
            except Exception:
                continue
