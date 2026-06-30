"""Ordered, failure-aware execution of Agent lifecycle hooks."""

import time

from src.core.hooks.models import (
    ALLOWED_ACTIONS, HookAction, HookContext, HookDecision,
    HookFailureMode, HookPoint,
)
from src.core.telemetry import emit_event


class HookRejected(RuntimeError):
    """Raised when a lifecycle hook intentionally rejects an operation."""


class HookDispatcher:
    def __init__(self, registry=None, *, enabled: bool = True) -> None:
        self.registry = registry
        self.enabled = enabled

    def dispatch(self, context: HookContext) -> tuple[HookContext, HookDecision]:
        if not self.enabled or self.registry is None:
            return context, HookDecision()
        current = context
        last = HookDecision()
        for spec in self.registry.matching(context.point, context.subject):
            started = time.monotonic()
            try:
                decision = spec.handler.handle(current)
                self._validate(context.point, decision)
            except Exception as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                emit_event(
                    "hook_failed", "hooks", "Lifecycle hook failed.",
                    {"hook_id": spec.hook_id, "point": context.point.value,
                     "error_type": type(exc).__name__},
                    level="error",
                    duration_ms=duration_ms,
                )
                if spec.failure_mode == HookFailureMode.CLOSED:
                    return current, HookDecision(
                        HookAction.REJECT,
                        reason=f"Hook {spec.hook_id} failed closed.",
                    )
                last = HookDecision(
                    HookAction.WARN,
                    reason=f"Hook {spec.hook_id} failed open.",
                )
                continue

            duration_seconds = time.monotonic() - started
            if duration_seconds > spec.timeout_seconds:
                emit_event(
                    "hook_timeout", "hooks", "Lifecycle hook exceeded timeout.",
                    {"hook_id": spec.hook_id, "point": context.point.value,
                     "timeout_seconds": spec.timeout_seconds},
                    level="error",
                    duration_ms=int(duration_seconds * 1000),
                )
                if spec.failure_mode == HookFailureMode.CLOSED:
                    return current, HookDecision(
                        HookAction.REJECT,
                        reason=f"Hook {spec.hook_id} exceeded its timeout.",
                    )
                last = HookDecision(
                    HookAction.WARN,
                    reason=f"Hook {spec.hook_id} exceeded its timeout but failed open.",
                )
                continue

            emit_event(
                "hook_finished", "hooks", "Lifecycle hook finished.",
                {"hook_id": spec.hook_id, "point": context.point.value,
                 "action": decision.action.value},
                duration_ms=int(duration_seconds * 1000),
            )
            last = decision
            if decision.action == HookAction.REPLACE:
                current = current.with_payload(_merge_payload(current.payload, decision.payload or {}))
                continue
            if decision.action in {
                HookAction.REJECT, HookAction.DENY,
                HookAction.ALLOW_ONCE, HookAction.ASK_USER,
            }:
                return current, decision
        return current, last

    def require(self, context: HookContext) -> HookContext:
        updated, decision = self.dispatch(context)
        if decision.action in {HookAction.REJECT, HookAction.DENY}:
            raise HookRejected(decision.reason or f"{context.point.value} hook rejected the operation.")
        return updated

    @staticmethod
    def _validate(point: HookPoint, decision: HookDecision) -> None:
        if not isinstance(decision, HookDecision):
            raise TypeError("Hook handler must return HookDecision")
        if decision.action not in ALLOWED_ACTIONS[point]:
            raise ValueError(f"Action {decision.action.value} is invalid for {point.value}")


NOOP_HOOK_DISPATCHER = HookDispatcher(enabled=False)


def _merge_payload(original, replacement):
    """Merge hook replacement fields without dropping unrelated context."""
    merged = dict(original)
    for key, value in dict(replacement).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_payload(merged[key], value)
        else:
            merged[key] = value
    return merged