"""Application service for approvals and Session approval-mode settings."""

from src.core.tools.security.models import ApprovalResponse, ToolApprovalMode


class ToolApprovalService:
    def __init__(
        self,
        *,
        repository,
        session_store,
        strategy_registry=None,
        default_mode: str = ToolApprovalMode.MANUAL.value,
    ) -> None:
        self.repository = repository
        self.session_store = session_store
        self.strategy_registry = strategy_registry
        try:
            self.default_mode = self._strategy(default_mode).name
        except ValueError:
            self.default_mode = ToolApprovalMode.MANUAL.value

    def list_pending(self, workspace_root: str, session_name: str) -> dict:
        session = self._session(workspace_root, session_name)
        requests = self.repository.list_pending(
            workspace_id=str(session.workspace.workspace_id),
            session_id=str(session.session_id),
        )
        return {"requests": requests}

    def get_mode(self, workspace_root: str, session_name: str) -> dict:
        return self.describe_session(self._session(workspace_root, session_name))

    def set_mode(
        self,
        workspace_root: str,
        session_name: str,
        mode: str,
        *,
        acknowledge_risk: bool = False,
    ) -> dict:
        session = self._session(workspace_root, session_name)
        normalized = str(mode).strip().lower()
        override = None if normalized == "inherit" else normalized
        if override is not None:
            self._strategy(override)
        if override == ToolApprovalMode.ACCEPT_ALL.value and not acknowledge_risk:
            raise ValueError(
                "accept_all requires acknowledge_risk=true because destructive "
                "ASK requests will execute automatically."
            )
        self.repository.set_session_mode(
            str(session.workspace.workspace_id),
            str(session.session_id),
            override,
        )
        result = self.describe_session(session)
        result["existing_pending_unchanged"] = result["pending_count"] > 0
        return result

    def describe_session(self, session) -> dict:
        workspace_id = str(session.workspace.workspace_id)
        session_id = str(session.session_id)
        override = self.repository.get_session_mode(workspace_id, session_id)
        try:
            effective = self._strategy(override or self.default_mode).name
        except ValueError:
            effective = ToolApprovalMode.MANUAL.value
        pending = self.repository.list_pending(
            workspace_id=workspace_id,
            session_id=session_id,
        )
        return {
            "schema_version": 1,
            "default_mode": self.default_mode,
            "override_mode": override,
            "effective_mode": effective,
            "supported_modes": list(
                self.strategy_registry.names()
                if self.strategy_registry is not None
                else sorted(item.value for item in ToolApprovalMode)
            ),
            "pending_count": len(pending),
        }

    def prepare_response(
        self,
        workspace_root: str,
        session_name: str,
        request_id: str,
        response: str,
    ) -> dict:
        session = self._session(workspace_root, session_name)
        pending = self.repository.get_pending(request_id)
        if pending is None:
            raise ValueError("Tool approval request is missing or already resolved.")
        if pending["workspace_id"] != str(session.workspace.workspace_id):
            raise PermissionError("Approval request belongs to another Workspace.")
        if pending["session_id"] != str(session.session_id):
            raise PermissionError("Approval request belongs to another Session.")
        try:
            selected = ApprovalResponse(response)
        except ValueError as exc:
            supported = ", ".join(item.value for item in ApprovalResponse)
            raise ValueError(
                f"Unsupported approval response {response!r}; expected one of: {supported}."
            ) from exc
        if selected.scope != "once" and not pending["persistable"]:
            raise ValueError("This command cannot create a persistent permission rule.")
        return {"request_id": request_id, "response": selected.value}

    def _session(self, workspace_root: str, session_name: str):
        workspace = self.session_store.resolve_workspace(workspace_root)
        existing = self.session_store.find_session(workspace, session_name)
        if existing is None:
            raise ValueError("Session does not exist.")
        if existing[1]:
            raise ValueError("Archived Session cannot resolve tool approvals.")
        return existing[0]

    def _strategy(self, mode: str):
        if self.strategy_registry is None:
            if mode not in {item.value for item in ToolApprovalMode}:
                raise ValueError(f"Unsupported tool approval mode: {mode!r}.")
            return type("BuiltInApprovalMode", (), {"name": mode})()
        return self.strategy_registry.get(mode)
