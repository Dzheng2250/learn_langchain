"""SQLite adapter for durable tool approvals and permission rules."""

import json
from uuid import uuid4

from src.core.tools.security.command_rules import command_rule_argv
from src.core.tools.security.models import ApprovalResponse


class SQLiteToolApprovalRepository:
    def __init__(self, database) -> None:
        self.database = database

    def create_request(self, context, decision, args_summary: dict) -> dict:
        request_id = uuid4().hex
        capabilities = [item.value for item in decision.capabilities]
        with self.database.transaction() as conn:
            existing = conn.execute(
                """SELECT * FROM tool_approval_requests
                   WHERE execution_id=? AND tool_call_id=?""",
                (context.execution_id, context.tool_call_id),
            ).fetchone()
            if existing:
                return self._request_dict(existing)
            conn.execute(
                """INSERT INTO tool_approval_requests(
                       request_id, workspace_id, session_id, execution_id,
                       tool_call_id, tool_name, actor, args_summary,
                       capabilities, rule_key, persistable, reason
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    request_id, context.workspace_id, context.session_id,
                    context.execution_id, context.tool_call_id, context.tool_name,
                    context.actor, json.dumps(args_summary, ensure_ascii=False),
                    json.dumps(capabilities), decision.rule_key,
                    int(decision.persistable), decision.reason,
                ),
            )
            row = conn.execute(
                "SELECT * FROM tool_approval_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
        return self._request_dict(row)

    def apply_response(self, request_id, response, *, context, rule_key, persistable) -> None:
        if not isinstance(response, ApprovalResponse):
            response = ApprovalResponse(response)
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM tool_approval_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if not row or row["status"] != "pending":
                raise ValueError("Tool approval request is missing or already resolved.")
            if row["execution_id"] != context.execution_id or row["tool_call_id"] != context.tool_call_id:
                raise PermissionError("Approval request identity does not match the tool call.")
            updated = conn.execute(
                """UPDATE tool_approval_requests
                   SET status='resolved', response=?, resolved_at=CURRENT_TIMESTAMP
                   WHERE request_id=? AND status='pending'""",
                (response.value, request_id),
            )
            if updated.rowcount != 1:
                raise ValueError("Tool approval request was resolved concurrently.")
            if response.scope != "once":
                if not persistable or not rule_key:
                    raise PermissionError("This approval cannot be persisted.")
                session_id = context.session_id if response.scope == "session" else None
                existing_rule = conn.execute(
                    """SELECT rule_id FROM tool_permission_rules
                       WHERE workspace_id=? AND session_id IS ?
                         AND tool_name=? AND rule_key=?""",
                    (context.workspace_id, session_id, context.tool_name, rule_key),
                ).fetchone()
                effect = "allow" if response.allowed else "deny"
                if existing_rule:
                    conn.execute(
                        """UPDATE tool_permission_rules SET effect=?,
                           created_from_request_id=?, updated_at=CURRENT_TIMESTAMP
                           WHERE rule_id=?""",
                        (effect, request_id, existing_rule["rule_id"]),
                    )
                else:
                    conn.execute(
                        """INSERT INTO tool_permission_rules(
                               rule_id, workspace_id, session_id, tool_name,
                               rule_key, effect, created_from_request_id
                           ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            uuid4().hex, context.workspace_id, session_id,
                            context.tool_name, rule_key, effect, request_id,
                        ),
                    )
            conn.execute(
                """INSERT OR IGNORE INTO tool_approval_audit(
                       audit_id, request_id, workspace_id, session_id,
                       execution_id, tool_call_id, tool_name, response
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uuid4().hex, request_id, context.workspace_id,
                    context.session_id, context.execution_id,
                    context.tool_call_id, context.tool_name, response.value,
                ),
            )

    def matching_rule(self, context, rule_key: str) -> str | None:
        command_argv = command_rule_argv(rule_key)
        with self.database.connect() as conn:
            if rule_key.startswith("workspace-write:"):
                rows = conn.execute(
                    """SELECT effect, session_id, rule_key FROM tool_permission_rules
                       WHERE workspace_id=? AND tool_name=?
                         AND rule_key LIKE 'workspace-write:%'
                         AND (session_id IS NULL OR session_id=?)""",
                    (context.workspace_id, context.tool_name, context.session_id),
                ).fetchall()
            elif command_argv is None:
                rows = conn.execute(
                    """SELECT effect, session_id, rule_key FROM tool_permission_rules
                       WHERE workspace_id=? AND tool_name=? AND rule_key=?
                         AND (session_id IS NULL OR session_id=?)""",
                    (context.workspace_id, context.tool_name, rule_key, context.session_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT effect, session_id, rule_key FROM tool_permission_rules
                       WHERE workspace_id=? AND tool_name=?
                         AND rule_key LIKE 'command-argv:%'
                         AND (session_id IS NULL OR session_id=?)""",
                    (context.workspace_id, context.tool_name, context.session_id),
                ).fetchall()
        effects = []
        for row in rows:
            if rule_key.startswith("workspace-write:"):
                requested_scope = rule_key.rsplit(":", 1)[-1]
                stored_scope = row["rule_key"].rsplit(":", 1)[-1]
                matches = (
                    requested_scope == stored_scope
                    or requested_scope.startswith(stored_scope.rstrip("/") + "/")
                )
            else:
                stored_argv = command_rule_argv(row["rule_key"])
                matches = command_argv is None or (
                    stored_argv is not None
                    and command_argv[:len(stored_argv)] == stored_argv
                )
            if matches:
                effects.append(row["effect"])
        if "deny" in effects:
            return "deny"
        return "allow" if "allow" in effects else None

    def list_pending(self, *, workspace_id=None, session_id=None) -> list[dict]:
        clauses = ["r.status='pending'", "e.status NOT IN ('completed', 'discarded', 'unrecoverable_checkpoint')"]
        values = []
        if workspace_id:
            clauses.append("r.workspace_id=?")
            values.append(workspace_id)
        if session_id:
            clauses.append("r.session_id=?")
            values.append(session_id)
        sql = (
            "SELECT r.* FROM tool_approval_requests r "
            "JOIN executions e ON e.execution_id=r.execution_id WHERE "
            + " AND ".join(clauses)
        )
        with self.database.connect() as conn:
            rows = conn.execute(sql + " ORDER BY created_at", values).fetchall()
        return [self._request_dict(row) for row in rows]

    def get_pending(self, request_id: str) -> dict | None:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT r.* FROM tool_approval_requests r
                   JOIN executions e ON e.execution_id=r.execution_id
                   WHERE r.request_id=? AND r.status='pending'
                     AND e.status NOT IN ('completed', 'discarded', 'unrecoverable_checkpoint')""",
                (request_id,),
            ).fetchone()
        return self._request_dict(row) if row else None

    @staticmethod
    def _request_dict(row) -> dict:
        return {
            "request_id": row["request_id"],
            "workspace_id": row["workspace_id"],
            "session_id": row["session_id"],
            "execution_id": row["execution_id"],
            "tool_call_id": row["tool_call_id"],
            "tool": row["tool_name"],
            "actor": row["actor"],
            "args": json.loads(row["args_summary"]),
            "capabilities": json.loads(row["capabilities"]),
            "persistable": bool(row["persistable"]),
            "reason": row["reason"],
            "status": row["status"],
            "response": row["response"],
            "created_at": row["created_at"],
        }
