"""Application service for uncertain tool invocation recovery."""


class ToolRecoveryService:
    """Expose safe recovery decisions without leaking SQLite to transports."""

    def __init__(self, *, repository, session_store, execution_repository) -> None:
        self.repository = repository
        self.session_store = session_store
        self.execution_repository = execution_repository

    def list_pending(self, workspace_root: str, session_name: str) -> dict:
        session = self._session(workspace_root, session_name)
        items = self.repository.list_uncertain(
            str(session.workspace.workspace_id), str(session.session_id)
        )
        return {"schema_version": 1, "items": items, "count": len(items)}

    def get(self, workspace_root: str, session_name: str, tool_call_id: str) -> dict:
        session = self._session(workspace_root, session_name)
        pending = self.execution_repository.get_pending(session)
        if pending is None:
            raise ValueError("Session has no pending execution.")
        item = self.repository.get(pending.execution_id, tool_call_id)
        if item is None:
            raise ValueError("Tool recovery record was not found.")
        return {"schema_version": 1, "item": item}

    def prepare_response(
        self,
        workspace_root: str,
        session_name: str,
        tool_call_id: str,
        action: str,
    ) -> dict:
        session = self._session(workspace_root, session_name)
        pending = self.execution_repository.get_pending(session)
        if pending is None or pending.stop_reason != "tool_recovery_required":
            raise ValueError("Session is not waiting for tool recovery.")
        self.repository.resolve(pending.execution_id, tool_call_id, action)
        return {
            "type": "tool_recovery",
            "execution_id": pending.execution_id,
            "tool_call_id": tool_call_id,
            "action": action,
        }

    def _session(self, workspace_root: str, session_name: str):
        workspace = self.session_store.resolve_workspace(workspace_root)
        session, _created = self.session_store.resolve_session(workspace, session_name)
        return session
