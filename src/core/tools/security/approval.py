"""Approval service and persistence contract."""

from typing import Protocol

from src.core.common.redaction import sanitize_value
from src.core.telemetry import emit_event

from src.core.tools.security.models import ApprovalResponse


class ApprovalRepository(Protocol):
    def create_request(self, context, decision, args_summary: dict) -> dict: ...
    def apply_response(self, request_id, response, *, context, rule_key, persistable) -> None: ...
    def matching_rule(self, context, rule_key: str) -> str | None: ...
    def list_pending(self, *, workspace_id=None, session_id=None) -> list[dict]: ...
    def get_pending(self, request_id: str) -> dict | None: ...


class ApprovalService:
    def __init__(self, repository: ApprovalRepository) -> None:
        self.repository = repository

    def request(self, context, decision) -> dict:
        request = self.repository.create_request(
            context, decision, _safe_args_summary(context.args)
        )
        emit_event(
            "tool_approval_requested", "tool_policy", "Tool approval required.",
            {"tool": context.tool_name, "request_id": request["request_id"]},
        )
        return request

    def resolve_interrupt(self, context, decision, request_id, raw_response) -> bool:
        payload = raw_response if isinstance(raw_response, dict) else {}
        if payload.get("request_id") != request_id:
            raise PermissionError("Approval response does not match the pending request.")
        response = ApprovalResponse(payload.get("response", "deny_once"))
        if response.scope != "once" and not decision.persistable:
            raise PermissionError("This call cannot create a persistent permission rule.")
        self.repository.apply_response(
            request_id,
            response,
            context=context,
            rule_key=decision.rule_key,
            persistable=decision.persistable,
        )
        emit_event(
            "tool_approval_resolved", "tool_policy", "Tool approval resolved.",
            {"tool": context.tool_name, "request_id": request_id, "response": response.value},
        )
        return response.allowed


def _safe_args_summary(args: dict) -> dict:
    return sanitize_value(args, text_limit=500, list_limit=20)
