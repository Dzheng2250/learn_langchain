"""Approval service and persistence contract."""

from typing import Protocol

from src.core.common.redaction import sanitize_value
from src.core.telemetry import emit_event

from src.core.tools.security.models import ApprovalResponse
from src.core.tools.workspace_patch import parse_workspace_patch


class ApprovalRepository(Protocol):
    def create_request(
        self,
        context,
        decision,
        args_summary: dict,
        *,
        approval_mode: str,
    ) -> dict: ...
    def apply_response(
        self,
        request_id,
        response,
        *,
        context,
        rule_key,
        persistable,
        decision_source: str,
        approval_mode: str,
    ) -> None: ...
    def matching_rule(self, context, rule_key: str) -> str | None: ...
    def list_pending(self, *, workspace_id=None, session_id=None) -> list[dict]: ...
    def get_pending(self, request_id: str) -> dict | None: ...


class ApprovalService:
    def __init__(self, repository: ApprovalRepository) -> None:
        self.repository = repository

    def request(self, context, decision, *, approval_mode: str = "manual") -> dict:
        request = self.repository.create_request(
            context,
            decision,
            _safe_args_summary(context.tool_name, context.args),
            approval_mode=approval_mode,
        )
        emit_event(
            "tool_approval_requested", "tool_policy", "Tool approval required.",
            {"tool": context.tool_name, "request_id": request["request_id"]},
        )
        return request

    def resolve_interrupt(
        self,
        context,
        decision,
        request_id,
        raw_response,
        *,
        approval_mode: str = "manual",
    ) -> bool:
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
            decision_source="user",
            approval_mode=approval_mode,
        )
        emit_event(
            "tool_approval_resolved", "tool_policy", "Tool approval resolved.",
            {"tool": context.tool_name, "request_id": request_id, "response": response.value},
        )
        return response.allowed

    def resolve_automatic(
        self,
        context,
        decision,
        request_id,
        response,
        *,
        approval_mode: str,
    ) -> bool:
        self.repository.apply_response(
            request_id,
            response,
            context=context,
            rule_key=decision.rule_key,
            persistable=decision.persistable,
            decision_source="automatic",
            approval_mode=approval_mode,
        )
        emit_event(
            "tool_approval_auto_resolved",
            "tool_policy",
            "Tool approval resolved automatically.",
            {
                "tool": context.tool_name,
                "request_id": request_id,
                "response": response.value,
                "approval_mode": approval_mode,
            },
        )
        return response.allowed

    def allow_once_from_hook(
        self,
        context,
        decision,
        *,
        approval_mode: str = "manual",
    ) -> bool:
        """Persist a hook-granted single-use approval for resume idempotency."""
        request = self.request(context, decision, approval_mode=approval_mode)
        if request.get("status") == "resolved":
            return str(request.get("response") or "").startswith("allow_")
        self.repository.apply_response(
            request["request_id"],
            ApprovalResponse.ALLOW_ONCE,
            context=context,
            rule_key=decision.rule_key,
            persistable=decision.persistable,
            decision_source="hook",
            approval_mode=approval_mode,
        )
        emit_event(
            "tool_approval_resolved", "tool_policy", "Tool approval resolved by hook.",
            {"tool": context.tool_name, "request_id": request["request_id"],
             "response": ApprovalResponse.ALLOW_ONCE.value},
        )
        return True


def _safe_args_summary(tool_name: str, args: dict) -> dict:
    if tool_name == "apply_workspace_patch":
        patch_text = str(args.get("patch") or "")
        try:
            parsed = parse_workspace_patch(patch_text)
            return {
                "paths": list(parsed.paths),
                "file_count": len(parsed.files),
                "hunk_count": parsed.hunk_count,
                "patch_chars": len(patch_text),
            }
        except ValueError:
            return {"patch": f"<{len(patch_text)} chars omitted; invalid patch>"}
    safe_args = dict(args)
    for key in ("content", "old_text", "new_text", "patch"):
        value = safe_args.get(key)
        if isinstance(value, str):
            safe_args[key] = f"<{len(value)} chars omitted>"
    return sanitize_value(safe_args, text_limit=500, list_limit=20)
