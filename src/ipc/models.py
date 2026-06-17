"""Strict JSON-RPC 2.0 wire models shared by clients and Core."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base wire model that rejects unknown protocol fields."""
    model_config = ConfigDict(extra="forbid")


class JsonRpcRequest(StrictModel):
    """Validated JSON-RPC request envelope shared by CLI and Core."""
    jsonrpc: Literal["2.0"]
    id: str | int
    method: str = Field(min_length=1, max_length=100)
    params: dict[str, Any] = Field(default_factory=dict)


class AuthenticatedParams(StrictModel):
    """Base parameters required by every privileged Core RPC method."""
    auth_token: str = Field(min_length=1)


class PingParams(AuthenticatedParams):
    """Authenticated parameters for ``core.ping``."""
    pass


class ShutdownParams(AuthenticatedParams):
    """Authenticated parameters for ``core.shutdown``."""
    pass


class ChatParams(AuthenticatedParams):
    """Workspace-scoped input accepted by ``agent.chat``."""
    workspace_root: str = Field(min_length=1, max_length=4000)
    session_name: str = Field(default="default", min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=200_000)
    goal_mode: bool = False


class SessionParams(AuthenticatedParams):
    """Workspace-scoped Session identity accepted by Session control methods."""

    workspace_root: str = Field(min_length=1, max_length=4000)
    session_name: str = Field(default="default", min_length=1, max_length=200)


class SessionDeleteParams(SessionParams):
    """Parameters for archiving or permanently deleting one Session."""

    hard_delete: bool = False


class SessionResumeParams(SessionParams):
    """Optional guidance supplied while resuming a recoverable execution."""

    instruction: str = Field(default="", max_length=20_000)


class JsonRpcError(StrictModel):
    """Standard JSON-RPC error object."""
    code: int
    message: str
    data: Any | None = None


class JsonRpcSuccess(StrictModel):
    """Standard JSON-RPC success response."""
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int
    result: Any


class JsonRpcErrorResponse(StrictModel):
    """Standard JSON-RPC error response."""
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None
    error: JsonRpcError


class AgentEventNotification(StrictModel):
    """Server-side streaming notification associated with an Agent request."""
    jsonrpc: Literal["2.0"] = "2.0"
    method: Literal["agent.event"] = "agent.event"
    params: dict[str, Any]
