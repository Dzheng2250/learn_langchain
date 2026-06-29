"""Stable models for tool interception, policy, and approvals."""

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from src.core.tools.catalog import ToolCapability, ToolSpec


class PolicyAction(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class ApprovalResponse(StrEnum):
    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    ALLOW_WORKSPACE = "allow_workspace"
    DENY_ONCE = "deny_once"
    DENY_SESSION = "deny_session"
    DENY_WORKSPACE = "deny_workspace"

    @property
    def allowed(self) -> bool:
        return self.value.startswith("allow_")

    @property
    def scope(self) -> str:
        return self.value.rsplit("_", 1)[1]


class ToolExecutionStatus(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True)
class ToolCallContext:
    tool_name: str
    tool_call_id: str
    args: dict[str, Any]
    workspace_id: str
    session_id: str
    execution_id: str | None
    run_id: str | None
    actor: str
    spec: ToolSpec
    workspace_root: str = ""

    def with_args(self, args: dict[str, Any]):
        return replace(self, args=dict(args))


@dataclass(frozen=True)
class PolicyDecision:
    action: PolicyAction
    reason: str = ""
    rule_key: str = ""
    persistable: bool = False
    capabilities: frozenset[ToolCapability] = frozenset()


@dataclass(frozen=True)
class ToolExecutionResult:
    status: ToolExecutionStatus
    value: Any = None
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
