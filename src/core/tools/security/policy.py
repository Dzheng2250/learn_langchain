"""Dynamic policy evaluation for registered tool capabilities."""

from typing import Protocol

from src.core.tools.catalog import ApprovalRequirement, SandboxMode
from src.core.tools.security.models import PolicyAction, PolicyDecision


class ToolGuardian(Protocol):
    """Optional advisor that may only tighten a policy decision."""

    def evaluate(self, context) -> PolicyDecision | None: ...


class DefaultToolPolicyEngine:
    def __init__(self, rules, *, approval_enabled=True, host_execution_enabled=False, guardian=None) -> None:
        self.rules = rules
        self.approval_enabled = approval_enabled
        self.host_execution_enabled = host_execution_enabled
        self.guardian = guardian

    def evaluate(self, context, *, rule_key="", persistable=False):
        spec = context.spec
        if spec.approval == ApprovalRequirement.FORBIDDEN:
            return self._make(PolicyAction.DENY, context, "Tool is forbidden.")
        if spec.sandbox == SandboxMode.HOST_FULL_ACCESS and not self.host_execution_enabled:
            return self._make(PolicyAction.DENY, context, "Host execution is disabled.")
        stored = self.rules.matching_rule(context, rule_key) if rule_key else None
        if stored == "deny":
            return self._make(PolicyAction.DENY, context, "Stored rule denied this call.")
        if stored == "allow" and spec.approval != ApprovalRequirement.ALWAYS:
            return self._make(PolicyAction.ALLOW, context, "Stored rule allowed this call.")
        action = (
            PolicyAction.ALLOW
            if spec.approval == ApprovalRequirement.NONE
            or (spec.approval == ApprovalRequirement.POLICY and not self.approval_enabled)
            else PolicyAction.ASK
        )
        decision = self._make(
            action, context, "Tool requires user approval.", rule_key, persistable
        )
        if self.guardian is None:
            return decision
        guardian = self.guardian.evaluate(context)
        rank = {PolicyAction.ALLOW: 0, PolicyAction.ASK: 1, PolicyAction.DENY: 2}
        return guardian if guardian and rank[guardian.action] > rank[action] else decision

    @staticmethod
    def _make(action, context, reason, rule_key="", persistable=False):
        return PolicyDecision(
            action, reason, rule_key, persistable, context.spec.capabilities
        )
