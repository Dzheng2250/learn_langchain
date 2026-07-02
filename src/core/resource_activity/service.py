"""Application service for frontend-neutral resource activity queries."""
class ResourceActivityQueryService:
    def __init__(self, repository, workspace_repository): self.repository=repository; self.workspaces=workspace_repository
    def _scope(self, *, execution_id="", workspace_root="", session_name="default", turn_index=None):
        if execution_id: return {"execution_id":execution_id}
        if not workspace_root or turn_index is None: raise ValueError("execution_id or workspace_root/session_name/turn_index is required")
        workspace=self.workspaces.resolve(workspace_root)
        found=self.workspaces.get_session_by_name(workspace,session_name,include_archived=True)
        if found is None: raise ValueError("Session not found")
        session,_=found
        return {"workspace_id":str(workspace.workspace_id),"session_id":str(session.session_id),"turn_index":turn_index}
    def summary(self, **params): return self.repository.summary(**self._scope(**params)).to_dict()
    def summary_for_run(self, run_id): return self.repository.summary_for_run(run_id).to_dict()
    def list(self, **params):
        scope=self._scope(execution_id=params.pop("execution_id",""),workspace_root=params.pop("workspace_root",""),session_name=params.pop("session_name","default"),turn_index=params.pop("turn_index",None))
        return self.repository.list(**scope,**params)