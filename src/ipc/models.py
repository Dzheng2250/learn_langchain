"""Strict JSON-RPC 2.0 wire models shared by clients and Core."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
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
