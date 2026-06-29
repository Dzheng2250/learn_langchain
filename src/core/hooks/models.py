"""Typed vocabulary for lifecycle hooks exposed by the Agent runtime."""

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Mapping


class HookPoint(StrEnum):
    SESSION_START = "SessionStart"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PRE_TOOL_USE = "PreToolUse"
    PERMISSION_REQUEST = "PermissionRequest"
    POST_TOOL_USE = "PostToolUse"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"
    STOP = "Stop"


class HookAction(StrEnum):
    CONTINUE = "continue"
    REPLACE = "replace"
    REJECT = "reject"
    ALLOW_ONCE = "allow_once"
    ASK_USER = "ask_user"
    DENY = "deny"


class HookFailureMode(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True)
class HookContext:
    point: HookPoint
    subject: str = ""
    workspace_id: str = ""
    session_id: str = ""
    execution_id: str = ""
    run_id: str = ""
    workspace_root: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)

    def with_payload(self, payload: Mapping[str, Any]) -> "HookContext":
        return replace(self, payload=dict(payload))


@dataclass(frozen=True)
class HookDecision:
    action: HookAction = HookAction.CONTINUE
    payload: Mapping[str, Any] | None = None
    reason: str = ""


@dataclass(frozen=True)
class HookSpec:
    hook_id: str
    point: HookPoint
    handler: object
    matcher: str = "*"
    priority: int = 100
    failure_mode: HookFailureMode = HookFailureMode.OPEN


ALLOWED_ACTIONS = {
    HookPoint.SESSION_START: {HookAction.CONTINUE},
    HookPoint.USER_PROMPT_SUBMIT: {HookAction.CONTINUE, HookAction.REPLACE, HookAction.REJECT},
    HookPoint.PRE_TOOL_USE: {HookAction.CONTINUE, HookAction.REPLACE, HookAction.REJECT},
    HookPoint.PERMISSION_REQUEST: {
        HookAction.CONTINUE, HookAction.ALLOW_ONCE, HookAction.ASK_USER,
        HookAction.DENY,
    },
    HookPoint.POST_TOOL_USE: {HookAction.CONTINUE},
    HookPoint.PRE_COMPACT: {HookAction.CONTINUE, HookAction.REPLACE, HookAction.REJECT},
    HookPoint.POST_COMPACT: {HookAction.CONTINUE},
    HookPoint.SUBAGENT_START: {HookAction.CONTINUE, HookAction.REPLACE, HookAction.REJECT},
    HookPoint.SUBAGENT_STOP: {HookAction.CONTINUE, HookAction.REPLACE},
    HookPoint.STOP: {HookAction.CONTINUE, HookAction.REJECT},
}
