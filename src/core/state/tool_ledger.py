"""Durable idempotency ledger for tool invocations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from langchain_core.messages import ToolMessage, message_to_dict, messages_from_dict

from src.core.resource_activity.observation import file_snapshot
from src.core.workspace.resolver import canonicalize_workspace, resolve_workspace_target


SAFE_RETRY = "safe_retry"
RECONCILE = "reconcile"


class ToolRecoveryRequired(RuntimeError):
    """A previous invocation may have side effects and cannot be replayed safely."""

    def __init__(self, message: str, *, tool_call_id: str = "", tool_name: str = "") -> None:
        super().__init__(message)
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name


@dataclass(frozen=True)
class ToolLedgerClaim:
    action: str
    message: ToolMessage | None = None


class ToolLedgerRepository:
    """Claim tool calls and replay completed model-visible results."""

    def __init__(
        self,
        database,
        *,
        artifact_store=None,
        inline_result_bytes: int = 64 * 1024,
    ) -> None:
        self.database = database
        self.artifact_store = artifact_store
        self.inline_result_bytes = max(1024, int(inline_result_bytes))

    def replay_completed(self, context) -> ToolLedgerClaim:
        """Replay a terminal result without claiming or retrying the call.

        Batch ToolNodes restart from their graph checkpoint after an interrupt.
        This lookup lets them recover already committed results before repeating
        policy, approval, or budget work. Non-terminal rows still flow through
        ``claim`` so their replay policy and reconciliation rules remain active.
        """
        if not context.execution_id or not context.tool_call_id:
            return ToolLedgerClaim("execute")
        args_hash = _args_hash(context.args)
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM tool_ledger WHERE execution_id=? AND tool_call_id=?",
                (str(context.execution_id), context.tool_call_id),
            ).fetchone()
            if row is None:
                return ToolLedgerClaim("execute")
            if row["tool_name"] != context.tool_name or row["args_hash"] != args_hash:
                raise ToolRecoveryRequired(
                    "Tool call identity was reused with different arguments.",
                    tool_call_id=context.tool_call_id,
                    tool_name=context.tool_name,
                )
            if row["status"] not in {"succeeded", "failed"}:
                return ToolLedgerClaim("execute")
            payload = self._result_payload(row)
            if payload:
                return ToolLedgerClaim("replay", _deserialize_message(payload))
        with self.database.transaction() as conn:
            conn.execute(
                "UPDATE tool_ledger SET status='uncertain' "
                "WHERE execution_id=? AND tool_call_id=?",
                (str(context.execution_id), context.tool_call_id),
            )
        raise ToolRecoveryRequired(
            "A completed tool result is missing and cannot be replayed safely.",
            tool_call_id=context.tool_call_id,
            tool_name=context.tool_name,
        )

    def claim(self, context) -> ToolLedgerClaim:
        if not context.execution_id or not context.tool_call_id:
            return ToolLedgerClaim("execute")
        args_hash = _args_hash(context.args)
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM tool_ledger WHERE execution_id=? AND tool_call_id=?",
                (str(context.execution_id), context.tool_call_id),
            ).fetchone()
            if row is None:
                before_state = _capture_before_state(context)
                conn.execute(
                    """
                    INSERT INTO tool_ledger(
                        execution_id,tool_call_id,tool_name,risk,args_hash,effect,
                        replay_policy,status,args_preview,before_state,attempt_count,
                        run_id,slice_id
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        str(context.execution_id), context.tool_call_id,
                        context.tool_name, context.spec.risk.value, args_hash,
                        context.spec.effect.value, context.spec.replay_policy.value,
                        "running", _args_preview(context.args),
                        json.dumps(before_state, sort_keys=True), 1,
                        str(context.run_id or ""), context.slice_id,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM tool_ledger WHERE execution_id=? AND tool_call_id=?",
                    (str(context.execution_id), context.tool_call_id),
                ).fetchone()
                activities = self._applied_activities(conn, context)
                if not activities:
                    return ToolLedgerClaim("execute")
                if context.spec.replay_policy.value == RECONCILE:
                    reconciled = self._reconcile(
                        conn,
                        context,
                        row,
                        activities=activities,
                    )
                    if reconciled is not None:
                        return ToolLedgerClaim("replay", reconciled)
                conn.execute(
                    "UPDATE tool_ledger SET status='uncertain' "
                    "WHERE execution_id=? AND tool_call_id=?",
                    (str(context.execution_id), context.tool_call_id),
                )
            elif row["tool_name"] != context.tool_name or row["args_hash"] != args_hash:
                raise ToolRecoveryRequired(
                    "Tool call identity was reused with different arguments.",
                    tool_call_id=context.tool_call_id,
                    tool_name=context.tool_name,
                )
            elif row["status"] in {"succeeded", "failed"}:
                payload = self._result_payload(row)
                if payload:
                    return ToolLedgerClaim("replay", _deserialize_message(payload))
            else:
                policy = str(row["replay_policy"])
                if policy == SAFE_RETRY:
                    self._mark_retry(conn, context)
                    return ToolLedgerClaim("execute")
                if policy == RECONCILE:
                    reconciled = self._reconcile(conn, context, row)
                    if reconciled is not None:
                        return ToolLedgerClaim("replay", reconciled)
                    before_state = _json_object(row["before_state"])
                    if before_state and _current_state(context) == before_state:
                        self._mark_retry(conn, context)
                        return ToolLedgerClaim("execute")
            conn.execute(
                "UPDATE tool_ledger SET status='uncertain' WHERE execution_id=? AND tool_call_id=?",
                (str(context.execution_id), context.tool_call_id),
            )
        raise ToolRecoveryRequired(
            "A previous tool attempt may have produced side effects; review it before retrying.",
            tool_call_id=context.tool_call_id,
            tool_name=context.tool_name,
        )

    def finish(self, context, message: ToolMessage) -> None:
        if not context.execution_id or not context.tool_call_id:
            return
        status = "failed" if getattr(message, "status", None) == "error" else "succeeded"
        payload = json.dumps(message_to_dict(message), ensure_ascii=False)
        inline_payload = payload
        artifact_id = None
        if (
            self.artifact_store is not None
            and len(payload.encode("utf-8")) > self.inline_result_bytes
        ):
            artifact = self.artifact_store.put(
                payload,
                content_type="application/vnd.learn-agent.tool-message+json",
            )
            artifact_id = artifact.artifact_id
            inline_payload = ""
            self.artifact_store.add_reference(
                artifact_id,
                "tool_ledger",
                f"{context.execution_id}:{context.tool_call_id}",
            )
        preview = str(message.content)[:2000]
        with self.database.transaction() as conn:
            conn.execute(
                """
                UPDATE tool_ledger SET status=?,result_payload=?,result_preview=?,
                    after_state=?,artifact_id=?,finished_at=CURRENT_TIMESTAMP
                WHERE execution_id=? AND tool_call_id=?
                """,
                (
                    status, inline_payload, preview,
                    json.dumps(_current_state(context), sort_keys=True),
                    artifact_id,
                    str(context.execution_id), context.tool_call_id,
                ),
            )

    def _result_payload(self, row) -> str:
        payload = str(row["result_payload"] or "")
        if payload:
            return payload
        artifact_id = str(row["artifact_id"] or "")
        if artifact_id and self.artifact_store is not None:
            return self.artifact_store.get(artifact_id).decode("utf-8")
        return ""

    def mark_running_uncertain(self) -> int:
        """Fail closed for invocations left running by a prior daemon process."""
        with self.database.transaction() as conn:
            cursor = conn.execute(
                "UPDATE tool_ledger SET status='uncertain' WHERE status='running'"
            )
            return int(cursor.rowcount or 0)

    def list_uncertain(self, workspace_id: str, session_id: str) -> list[dict]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT l.execution_id,l.tool_call_id,l.tool_name,l.effect,
                       l.replay_policy,l.status,l.args_preview,l.attempt_count,
                       l.started_at,l.finished_at
                FROM tool_ledger l
                JOIN executions e ON e.execution_id=l.execution_id
                JOIN sessions s ON s.session_id=e.session_id
                WHERE e.workspace_id=? AND e.session_id=?
                  AND s.pending_execution_id=e.execution_id
                  AND l.status IN ('running','uncertain','legacy')
                ORDER BY l.started_at,l.tool_call_id
                """,
                (workspace_id, session_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, execution_id: str, tool_call_id: str) -> dict | None:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT execution_id,tool_call_id,tool_name,effect,replay_policy,
                       status,args_preview,attempt_count,started_at,finished_at
                FROM tool_ledger WHERE execution_id=? AND tool_call_id=?
                """,
                (execution_id, tool_call_id),
            ).fetchone()
        return dict(row) if row else None

    def resolve(self, execution_id: str, tool_call_id: str, action: str) -> None:
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM tool_ledger WHERE execution_id=? AND tool_call_id=?",
                (execution_id, tool_call_id),
            ).fetchone()
            if row is None or row["status"] not in {"running", "uncertain", "legacy"}:
                raise ValueError("Tool recovery request is no longer pending.")
            if action == "retry_once":
                conn.execute(
                    """
                    UPDATE tool_ledger SET replay_policy='safe_retry',status='uncertain'
                    WHERE execution_id=? AND tool_call_id=?
                    """,
                    (execution_id, tool_call_id),
                )
                return
            if action == "return_error":
                message = ToolMessage(
                    content=(
                        f"Tool {row['tool_name']} was not replayed because its prior "
                        "side effects could not be determined."
                    ),
                    name=row["tool_name"],
                    tool_call_id=tool_call_id,
                    status="error",
                    additional_kwargs={"tool_execution_status": "recovery_skipped"},
                )
                conn.execute(
                    """
                    UPDATE tool_ledger SET status='failed',result_payload=?,
                        result_preview=?,finished_at=CURRENT_TIMESTAMP
                    WHERE execution_id=? AND tool_call_id=?
                    """,
                    (
                        json.dumps(message_to_dict(message), ensure_ascii=False),
                        str(message.content), execution_id, tool_call_id,
                    ),
                )
                return
            raise ValueError(f"Unsupported tool recovery action: {action}")

    @staticmethod
    def _mark_retry(conn, context) -> None:
        conn.execute(
            """
            UPDATE tool_ledger SET status='running',attempt_count=attempt_count+1,
                run_id=?,slice_id=?,started_at=CURRENT_TIMESTAMP,finished_at=NULL
            WHERE execution_id=? AND tool_call_id=?
            """,
            (
                str(context.run_id or ""), context.slice_id,
                str(context.execution_id), context.tool_call_id,
            ),
        )

    @staticmethod
    def _applied_activities(conn, context):
        return conn.execute(
            """
            SELECT resource_uri,operation,after_digest,metadata
            FROM resource_activities
            WHERE execution_id=? AND tool_call_id=? AND change_state='applied'
            ORDER BY sequence
            """,
            (str(context.execution_id), context.tool_call_id),
        ).fetchall()

    def _reconcile(self, conn, context, row, *, activities=None) -> ToolMessage | None:
        activities = activities if activities is not None else self._applied_activities(
            conn, context
        )
        if not activities or not all(_activity_matches(context, item) for item in activities):
            return None
        message = ToolMessage(
            content=f"Recovered previously applied result for {context.tool_name}.",
            name=context.tool_name,
            tool_call_id=context.tool_call_id,
            additional_kwargs={"tool_execution_status": "recovered"},
        )
        payload = json.dumps(message_to_dict(message), ensure_ascii=False)
        conn.execute(
            """
            UPDATE tool_ledger SET status='succeeded',result_payload=?,result_preview=?,
                finished_at=CURRENT_TIMESTAMP
            WHERE execution_id=? AND tool_call_id=?
            """,
            (payload, str(message.content), str(context.execution_id), context.tool_call_id),
        )
        return message


def _args_hash(args: dict) -> str:
    raw = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _args_preview(args: dict) -> str:
    return json.dumps(sorted(str(key) for key in args), ensure_ascii=False)


def _deserialize_message(raw: str) -> ToolMessage:
    message = messages_from_dict([json.loads(raw)])[0]
    if not isinstance(message, ToolMessage):
        raise ValueError("Tool ledger payload is not a ToolMessage")
    return message


def _tracked_paths(context) -> dict[str, Path]:
    root = canonicalize_workspace(Path(context.workspace_root))
    paths = {}
    for key in ("path", "source", "destination"):
        value = context.args.get(key)
        if isinstance(value, str) and value.strip():
            paths[key] = resolve_workspace_target(root, value)
    return paths


def _capture_before_state(context) -> dict:
    return {
        key: {"path": str(path), **file_snapshot(path)}
        for key, path in _tracked_paths(context).items()
    }


def _current_state(context) -> dict:
    return _capture_before_state(context)


def _json_object(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _activity_matches(context, row) -> bool:
    uri = str(row["resource_uri"])
    if not uri.startswith("workspace://"):
        return False
    target = resolve_workspace_target(
        canonicalize_workspace(Path(context.workspace_root)),
        uri.removeprefix("workspace://"),
    )
    digest = str(row["after_digest"] or "")
    if digest:
        return target.is_file() and file_snapshot(target)["digest"] == digest
    metadata = _json_object(row["metadata"])
    if row["operation"] == "delete" or metadata.get("move_role") == "source":
        return not target.exists()
    return target.exists()
