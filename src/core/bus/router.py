"""Validated JSON-RPC method routing."""

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from src.ipc.auth import verify_token
from src.ipc.models import (
    JsonRpcError,
    JsonRpcErrorResponse,
    JsonRpcRequest,
    JsonRpcSuccess,
)


PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNAUTHORIZED = -32001

RpcHandler = Callable[[BaseModel, Any], Awaitable[Any]]


class RpcRouter:
    """Register validated RPC handlers and dispatch requests."""

    def __init__(self, auth_token: str) -> None:
        self.auth_token = auth_token
        self._handlers: dict[str, tuple[type[BaseModel], RpcHandler]] = {}

    def register(self, method: str, params_model: type[BaseModel], handler: RpcHandler) -> None:
        self._handlers[method] = (params_model, handler)

    async def dispatch(self, raw: dict, context: Any = None):
        request_id = raw.get("id") if isinstance(raw, dict) else None
        try:
            request = JsonRpcRequest.model_validate(raw)
        except ValidationError as exc:
            return error_response(request_id, INVALID_REQUEST, "Invalid Request", exc.errors())

        registration = self._handlers.get(request.method)
        if registration is None:
            return error_response(request.id, METHOD_NOT_FOUND, "Method not found")

        params_model, handler = registration
        try:
            params = params_model.model_validate(request.params)
        except ValidationError as exc:
            return error_response(request.id, INVALID_PARAMS, "Invalid params", exc.errors())

        received_token = getattr(params, "auth_token", "")
        if not verify_token(self.auth_token, received_token):
            return error_response(request.id, UNAUTHORIZED, "Unauthorized")

        try:
            result = await handler(params, context)
        except Exception as exc:
            return error_response(request.id, INTERNAL_ERROR, "Internal error", str(exc))
        return JsonRpcSuccess(id=request.id, result=result)


def error_response(request_id, code: int, message: str, data=None) -> JsonRpcErrorResponse:
    return JsonRpcErrorResponse(
        id=request_id,
        error=JsonRpcError(code=code, message=message, data=data),
    )
