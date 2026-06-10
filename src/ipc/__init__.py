"""Shared IPC contracts used by CLI and Core."""

from .models import (
    AgentEventNotification,
    ChatParams,
    JsonRpcErrorResponse,
    JsonRpcRequest,
    JsonRpcSuccess,
    PingParams,
    ShutdownParams,
)

__all__ = [
    "AgentEventNotification",
    "ChatParams",
    "JsonRpcErrorResponse",
    "JsonRpcRequest",
    "JsonRpcSuccess",
    "PingParams",
    "ShutdownParams",
]
