"""Pluggable strategies for resolving policy ASK decisions."""

from dataclasses import dataclass
from typing import Protocol

from src.core.telemetry import emit_event
from src.core.tools.security.models import (
    ApprovalResponse,
    ApprovalStrategyAction,
    ApprovalStrategyDecision,
    ToolApprovalMode,
)


class ApprovalStrategy(Protocol):
    name: str

    def decide(self, context, policy_decision) -> ApprovalStrategyDecision: ...


class ManualApprovalStrategy:
    name = ToolApprovalMode.MANUAL.value

    def decide(self, context, policy_decision) -> ApprovalStrategyDecision:
        return ApprovalStrategyDecision(ApprovalStrategyAction.WAIT)


class AcceptAllApprovalStrategy:
    name = ToolApprovalMode.ACCEPT_ALL.value

    def decide(self, context, policy_decision) -> ApprovalStrategyDecision:
        return ApprovalStrategyDecision(
            ApprovalStrategyAction.AUTO_ALLOW,
            ApprovalResponse.ALLOW_ONCE,
            "Automatically accepted by the Session approval mode.",
        )


class ApprovalStrategyRegistry:
    def __init__(self, strategies=()) -> None:
        self._strategies: dict[str, ApprovalStrategy] = {}
        for strategy in strategies or (
            ManualApprovalStrategy(),
            AcceptAllApprovalStrategy(),
        ):
            self.register(strategy)

    def register(self, strategy: ApprovalStrategy) -> None:
        name = str(strategy.name).strip().lower()
        if not name:
            raise ValueError("Approval strategy name must not be empty.")
        if not callable(getattr(strategy, "decide", None)):
            raise TypeError(f"Approval strategy {name!r} must define decide().")
        if name in self._strategies:
            raise ValueError(f"Duplicate approval strategy: {name}")
        self._strategies[name] = strategy

    def get(self, name: str) -> ApprovalStrategy:
        normalized = str(name).strip().lower()
        try:
            return self._strategies[normalized]
        except KeyError as exc:
            raise ValueError(f"Unsupported tool approval mode: {name!r}.") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._strategies))


class ApprovalModeStore(Protocol):
    def get_session_mode(self, workspace_id: str, session_id: str) -> str | None: ...

    def set_session_mode(
        self,
        workspace_id: str,
        session_id: str,
        mode: str | None,
    ) -> None: ...


class ApprovalModeResolver:
    def __init__(
        self,
        store: ApprovalModeStore,
        registry: ApprovalStrategyRegistry,
        *,
        default_mode: str,
    ) -> None:
        self.store = store
        self.registry = registry
        try:
            self.default_mode = self.registry.get(default_mode).name
        except ValueError:
            self.default_mode = ToolApprovalMode.MANUAL.value
            emit_event(
                "tool_approval_mode_invalid",
                "tool_policy",
                "Unknown global approval mode; falling back to manual.",
                {"mode": str(default_mode)},
                level="error",
            )

    def resolve(self, context) -> str:
        override = self.store.get_session_mode(context.workspace_id, context.session_id)
        selected = override or self.default_mode
        try:
            return self.registry.get(selected).name
        except ValueError:
            emit_event(
                "tool_approval_mode_invalid",
                "tool_policy",
                "Unknown Session approval mode; falling back to manual.",
                {"mode": str(selected), "session_id": context.session_id},
                level="error",
            )
            return ToolApprovalMode.MANUAL.value


@dataclass(frozen=True)
class ApprovalFlow:
    mode: str
    request: dict
    allowed: bool | None


class ApprovalCoordinator:
    """Apply one registered mode while preserving durable approval facts."""

    def __init__(
        self,
        approvals,
        resolver: ApprovalModeResolver,
        registry: ApprovalStrategyRegistry,
    ) -> None:
        self.approvals = approvals
        self.resolver = resolver
        self.registry = registry

    def begin(self, context, policy_decision) -> ApprovalFlow:
        current_mode = self.resolver.resolve(context)
        request = self.approvals.request(
            context,
            policy_decision,
            approval_mode=current_mode,
        )
        recorded_mode = str(request.get("approval_mode") or current_mode)
        try:
            strategy = self.registry.get(recorded_mode)
        except ValueError:
            emit_event(
                "tool_approval_mode_invalid",
                "tool_policy",
                "Stored approval request mode is unavailable; waiting for manual review.",
                {
                    "mode": recorded_mode,
                    "request_id": request.get("request_id"),
                },
                level="error",
            )
            recorded_mode = ToolApprovalMode.MANUAL.value
            strategy = self.registry.get(recorded_mode)
            request = {**request, "approval_mode": recorded_mode}
        decision = strategy.decide(context, policy_decision)
        if request.get("status") == "resolved":
            return ApprovalFlow(
                recorded_mode,
                request,
                str(request.get("response") or "").startswith("allow_"),
            )
        if decision.action == ApprovalStrategyAction.WAIT:
            return ApprovalFlow(recorded_mode, request, None)
        if decision.action not in {
            ApprovalStrategyAction.AUTO_ALLOW,
            ApprovalStrategyAction.AUTO_DENY,
        }:
            raise ValueError(
                f"Approval strategy {recorded_mode!r} returned an invalid action."
            )
        response = (
            ApprovalResponse.ALLOW_ONCE
            if decision.action == ApprovalStrategyAction.AUTO_ALLOW
            else ApprovalResponse.DENY_ONCE
        )
        if decision.response is not None and decision.response != response:
            raise ValueError(
                "Automatic approval strategies may only return one-time responses."
            )
        allowed = self.approvals.resolve_automatic(
            context,
            policy_decision,
            request["request_id"],
            response,
            approval_mode=recorded_mode,
        )
        return ApprovalFlow(recorded_mode, request, allowed)

    def resolve_manual(self, context, policy_decision, request, raw_response) -> bool:
        return self.approvals.resolve_interrupt(
            context,
            policy_decision,
            request["request_id"],
            raw_response,
            approval_mode=str(
                request.get("approval_mode") or ToolApprovalMode.MANUAL.value
            ),
        )

    def allow_once_from_hook(self, context, policy_decision) -> bool:
        mode = self.resolver.resolve(context)
        return self.approvals.allow_once_from_hook(
            context,
            policy_decision,
            approval_mode=mode,
        )
