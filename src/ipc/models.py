"""Strict JSON-RPC 2.0 wire models shared by clients and Core."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Base wire model that rejects unknown protocol fields."""
    model_config = ConfigDict(extra="forbid")


class JsonRpcRequest(StrictModel):
    jsonrpc: Literal["2.0"]
    id: str | int
    method: str = Field(min_length=1, max_length=100)
    params: dict[str, Any] = Field(default_factory=dict)


class AuthenticatedParams(StrictModel):
    auth_token: str = Field(min_length=1)


class PingParams(AuthenticatedParams):
    pass


class ShutdownParams(AuthenticatedParams):
    pass


class ChatParams(AuthenticatedParams):
    workspace_root: str = Field(min_length=1, max_length=4000)
    session_name: str = Field(default="default", min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=200_000)
    goal_mode: bool = False


class SessionParams(AuthenticatedParams):
    workspace_root: str = Field(min_length=1, max_length=4000)
    session_name: str = Field(default="default", min_length=1, max_length=200)


class SessionHistoryParams(SessionParams):
    before_turn: int | None = Field(default=None, ge=0)
    limit_turns: int = Field(default=30, ge=1, le=100)


class SessionDeleteParams(SessionParams):
    hard_delete: bool = False


class SessionResumeParams(SessionParams):
    instruction: str = Field(default="", max_length=20_000)
    retry_conditions: bool = False


class ApprovalResolveParams(SessionParams):
    request_id: str = Field(min_length=1, max_length=200)
    response: Literal[
        "allow_once", "allow_session", "allow_workspace",
        "deny_once", "deny_session", "deny_workspace",
    ]


class ApprovalModeSetParams(SessionParams):
    mode: str = Field(min_length=1, max_length=100)
    acknowledge_risk: bool = False


class ToolRecoveryGetParams(SessionParams):
    tool_call_id: str = Field(min_length=1, max_length=200)


class ToolRecoveryResolveParams(ToolRecoveryGetParams):
    action: Literal["retry_once", "return_error", "discard_execution"]


class ResourceActivityScopeParams(AuthenticatedParams):
    """Select resource activity by Execution or historical Session Turn."""
    execution_id: str = Field(default="", max_length=200)
    workspace_root: str = Field(default="", max_length=4000)
    session_name: str = Field(default="default", min_length=1, max_length=200)
    turn_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.execution_id or (self.workspace_root and self.turn_index is not None):
            return self
        raise ValueError("execution_id or workspace_root/session_name/turn_index is required")


class ResourceActivityListParams(ResourceActivityScopeParams):
    operation: Literal["", "read", "summarize", "create", "write", "move", "delete"] = ""
    change_state: Literal["", "observed", "proposed", "applied", "discarded"] = ""
    resource_uri: str = Field(default="", max_length=4000)
    cursor: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)


class JsonRpcError(StrictModel):
    code: int
    message: str
    data: Any | None = None


class JsonRpcSuccess(StrictModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int
    result: Any


class JsonRpcErrorResponse(StrictModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None
    error: JsonRpcError


class AgentEventNotification(StrictModel):
    jsonrpc: Literal["2.0"] = "2.0"
    method: Literal["agent.event"] = "agent.event"
    params: dict[str, Any]
