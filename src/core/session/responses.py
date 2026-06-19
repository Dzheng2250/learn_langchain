"""Response payload builders for Session lifecycle RPC methods."""


def archived_status_response(workspace, session) -> dict:
    """Return `session.status` payload for an archived Session."""
    return {
        "status": "archived",
        "workspace_id": str(workspace.workspace_id),
        "session_id": str(session.session_id),
        "session_name": session.session_name,
        "context_tokens": 0,
        "pending_execution": None,
        "execution_recoverable": False,
        "checkpoint_state": None,
        "maintenance": {
            "pending": 0,
            "running": 0,
            "failed": 0,
            "recent_failures": [],
        },
    }


def active_status_response(workspace, session, context_state, pending, maintenance: dict) -> dict:
    """Return `session.status` payload for an active Session."""
    return {
        "workspace_id": str(workspace.workspace_id),
        "session_id": str(session.session_id),
        "session_name": session.session_name,
        "context_tokens": context_state.context_tokens if context_state else 0,
        "pending_execution": pending.__dict__ if pending else None,
        "execution_recoverable": pending.recoverable if pending else False,
        "checkpoint_state": pending.checkpoint_state if pending else None,
        "maintenance": maintenance,
    }


def archived_discard_response(session) -> dict:
    """Return `session.discard` payload when the Session is archived."""
    return {
        "status": "archived",
        "workspace_id": str(session.workspace.workspace_id),
        "session_id": str(session.session_id),
        "session_name": session.session_name,
        "message": "Session is archived and has no active execution to discard.",
    }


def idle_discard_response(session) -> dict:
    """Return `session.discard` payload when no Execution is attached."""
    return {
        "status": "idle",
        "workspace_id": str(session.workspace.workspace_id),
        "session_id": str(session.session_id),
        "session_name": session.session_name,
        "message": "Session has no pending execution to discard.",
    }


def not_found_delete_response(workspace, session_name: str, *, hard_delete: bool) -> dict:
    """Return `session.delete` payload when the Session name is unknown."""
    return {
        "status": "not_found",
        "mode": "hard_delete" if hard_delete else "archive",
        "workspace_id": str(workspace.workspace_id),
        "session_name": session_name,
    }


def hard_delete_response(session, *, deleted: bool, checkpoint_threads_deleted: int) -> dict:
    """Return `session.delete --hard` payload."""
    return {
        "status": "deleted" if deleted else "not_found",
        "mode": "hard_delete",
        "workspace_id": str(session.workspace.workspace_id),
        "session_id": str(session.session_id),
        "session_name": session.session_name,
        "checkpoint_threads_deleted": checkpoint_threads_deleted,
    }


def archive_response(session, *, archived_now: bool, cleanup_enqueued: bool) -> dict:
    """Return default archive payload for `session.delete`."""
    return {
        "status": "archived" if archived_now else "already_archived",
        "mode": "archive",
        "workspace_id": str(session.workspace.workspace_id),
        "session_id": str(session.session_id),
        "session_name": session.session_name,
        "cleanup_enqueued": cleanup_enqueued,
    }


def archived_reset_response(workspace, session) -> dict:
    """Return `session.reset` payload when the Session is archived."""
    return {
        "status": "archived",
        "workspace_id": str(workspace.workspace_id),
        "session_id": str(session.session_id),
        "session_name": session.session_name,
        "recovered_messages": 0,
    }


def reset_response(session, recovered_messages: int) -> dict:
    """Return successful `session.reset` payload."""
    return {
        "status": "ok",
        "workspace_id": str(session.workspace.workspace_id),
        "session_id": str(session.session_id),
        "session_name": session.session_name,
        "recovered_messages": recovered_messages,
    }
