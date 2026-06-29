"""Application service for listing and resuming tool approvals."""

from src.core.tools.security.models import ApprovalResponse


class ToolApprovalService:
    def __init__(self, *, repository, session_store) -> None:
        self.repository = repository
        self.session_store = session_store

    def list_pending(self, workspace_root: str, session_name: str) -> dict:
        session = self._session(workspace_root, session_name)
        requests = self.repository.list_pending(
            workspace_id=str(session.workspace.workspace_id),
            session_id=str(session.session_id),
        )
        return {"requests": requests}

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
        selected = ApprovalResponse(response)
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
