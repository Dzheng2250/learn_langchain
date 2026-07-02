"""SQLite-backed authoritative resource activity ledger."""
import json
import logging
from collections import Counter
from uuid import uuid4

from src.core.resource_activity.models import (
    ChangeState,
    EvidenceStatus,
    ObservationMode,
    ResourceActivitySummary,
    ResourceObservation,
    ResourceOperation,
)
from src.core.resource_activity.observation import workspace_uri


class SQLiteResourceActivityRepository:
    def __init__(self, database, *, enabled=True, hash_enabled=True, max_items=1000):
        self.database = database
        self.enabled = enabled
        self.hash_enabled = hash_enabled
        self.max_items = max(1, int(max_items))

    def evidence_for(self, context) -> dict:
        path = context.args.get("resource_uri") or context.args.get("path") or context.args.get("source")
        if not path or not context.execution_id:
            return {"status": "not_applicable", "activity_id": None}
        value = str(path)
        uri = (
            value
            if value.startswith(("db://", "http://", "https://", "ssh://", "command://"))
            else workspace_uri(context.workspace_root, value)
        )
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT activity_id, observation_mode FROM resource_activities
                   WHERE execution_id=? AND resource_uri=? AND operation IN ('read','summarize')
                   ORDER BY sequence DESC LIMIT 1""",
                (str(context.execution_id), uri),
            ).fetchone()
        if row is None:
            return {"status": "missing", "activity_id": None, "resource_uri": uri}
        if row["observation_mode"] in ("range", "summary"):
            status = "partial"
        elif row["observation_mode"] in ("scope_only", "unknown"):
            status = "incomplete"
        else:
            status = "current"
        return {"status": status, "activity_id": row["activity_id"], "resource_uri": uri}

    def record(self, context, observation: ResourceObservation) -> str | None:
        if not self.enabled or not context.execution_id:
            return None
        execution_id = str(context.execution_id)
        with self.database.transaction() as conn:
            if observation.event_key:
                existing = conn.execute(
                    "SELECT activity_id FROM resource_activities WHERE execution_id=? AND event_key=?",
                    (execution_id, observation.event_key),
                ).fetchone()
                if existing is not None:
                    return existing["activity_id"]
            counter = conn.execute(
                "SELECT recorded_count,dropped_count FROM resource_activity_counters WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
            recorded = int(counter["recorded_count"]) if counter else 0
            dropped = int(counter["dropped_count"]) if counter else 0
            if recorded >= self.max_items:
                conn.execute(
                    """INSERT INTO resource_activity_counters(execution_id,recorded_count,dropped_count)
                       VALUES(?,?,1) ON CONFLICT(execution_id)
                       DO UPDATE SET dropped_count=dropped_count+1""",
                    (execution_id, recorded),
                )
                return None
            sequence = recorded + 1
            evidence, related = self._evidence(conn, execution_id, observation)
            activity_id = uuid4().hex
            conn.execute(
                """INSERT INTO resource_activities(
                    activity_id,sequence,workspace_id,session_id,turn_index,execution_id,slice_id,run_id,
                    tool_call_id,tool_name,actor,resource_uri,operation,observation_mode,change_state,
                    requested_range,observed_range,returned_bytes,resource_bytes,before_digest,after_digest,
                    before_lines,after_lines,evidence_status,related_activity_ids,metadata,event_key
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    activity_id, sequence, context.workspace_id, context.session_id, context.turn_index,
                    execution_id, context.slice_id, context.run_id or "", context.tool_call_id,
                    context.tool_name, context.actor, observation.resource_uri,
                    observation.operation.value, observation.observation_mode.value,
                    observation.change_state.value, json.dumps(observation.requested_range),
                    json.dumps(observation.observed_range), max(0, observation.returned_bytes),
                    max(0, observation.resource_bytes), observation.before_digest,
                    observation.after_digest, observation.before_lines, observation.after_lines,
                    evidence.value,
                    json.dumps(list(dict.fromkeys((*observation.related_activity_ids, *related)))),
                    json.dumps(observation.metadata, ensure_ascii=False), observation.event_key,
                ),
            )
            conn.execute(
                """INSERT INTO resource_activity_counters(execution_id,recorded_count,dropped_count)
                   VALUES(?,?,?) ON CONFLICT(execution_id)
                   DO UPDATE SET recorded_count=excluded.recorded_count""",
                (execution_id, sequence, dropped),
            )
        return activity_id

    @staticmethod
    def _evidence(conn, execution_id, observation):
        if observation.operation in {ResourceOperation.READ, ResourceOperation.SUMMARIZE, ResourceOperation.CREATE}:
            return EvidenceStatus.NOT_APPLICABLE, ()
        row = conn.execute(
            """SELECT activity_id,observation_mode,after_digest,before_digest FROM resource_activities
               WHERE execution_id=? AND resource_uri=? AND operation IN ('read','summarize')
               ORDER BY sequence DESC LIMIT 1""",
            (execution_id, observation.resource_uri),
        ).fetchone()
        if row is None:
            return EvidenceStatus.MISSING, ()
        mode = row["observation_mode"]
        if mode in (ObservationMode.SCOPE_ONLY.value, ObservationMode.UNKNOWN.value):
            return EvidenceStatus.INCOMPLETE, (row["activity_id"],)
        if mode in (ObservationMode.RANGE.value, ObservationMode.SUMMARY.value):
            return EvidenceStatus.PARTIAL, (row["activity_id"],)
        digest = row["after_digest"] or row["before_digest"]
        if observation.before_digest and digest and observation.before_digest != digest:
            return EvidenceStatus.STALE, (row["activity_id"],)
        return EvidenceStatus.CURRENT, (row["activity_id"],)

    def summary(self, *, execution_id=None, workspace_id=None, session_id=None, turn_index=None, run_id=None):
        with self.database.read_transaction() as conn:
            return self._summary_with_connection(
                conn,
                execution_id=execution_id,
                workspace_id=workspace_id,
                session_id=session_id,
                turn_index=turn_index,
                run_id=run_id,
            )

    def summary_for_run(self, run_id: str):
        with self.database.read_transaction() as conn:
            row = conn.execute(
                "SELECT execution_id FROM resource_activities WHERE run_id=? ORDER BY sequence LIMIT 1",
                (str(run_id),),
            ).fetchone()
            return self._summary_with_connection(
                conn,
                execution_id=row["execution_id"] if row else None,
                run_id=run_id,
            )

    def _summary_with_connection(
        self,
        conn,
        *,
        execution_id=None,
        workspace_id=None,
        session_id=None,
        turn_index=None,
        run_id=None,
    ):
        where, params = self._scope(execution_id, workspace_id, session_id, turn_index, run_id)
        rows = conn.execute(
            f"SELECT * FROM resource_activities WHERE {where} ORDER BY sequence", params
        ).fetchall()
        execution_ids = {str(row["execution_id"]) for row in rows}
        if execution_id:
            execution_ids.add(str(execution_id))
        dropped = 0
        if execution_ids:
            placeholders = ",".join("?" for _ in execution_ids)
            counters = conn.execute(
                f"SELECT dropped_count FROM resource_activity_counters WHERE execution_id IN ({placeholders})",
                tuple(execution_ids),
            ).fetchall()
            dropped = sum(int(counter["dropped_count"]) for counter in counters)

        read_rows = [row for row in rows if row["operation"] in ("read", "summarize")]
        terminal_changes = {}
        change_resources = {}
        for row in rows:
            state = row["change_state"]
            if state not in {"proposed", "applied", "discarded"}:
                continue
            metadata = self._json_object(row["metadata"], row["activity_id"])
            change_set_id = metadata.get("change_set_id")
            change_group_id = metadata.get("change_group_id")
            if change_set_id:
                key = ("change_set", change_set_id, row["resource_uri"])
            elif change_group_id:
                key = ("change_group", change_group_id)
            else:
                key = ("activity", row["activity_id"])
            terminal_changes[key] = row
            change_resources.setdefault(key, set()).add(row["resource_uri"])
        current_change_rows = list(terminal_changes.values())
        applied_keys = {
            key for key, row in terminal_changes.items()
            if row["change_state"] == "applied"
        }
        changed_resources = {
            uri for key in applied_keys for uri in change_resources[key]
        }
        states = Counter(row["change_state"] for row in current_change_rows)
        evidence = Counter(
            row["evidence_status"] for row in rows if row["evidence_status"] != "not_applicable"
        )
        modes = Counter(row["observation_mode"] for row in read_rows)
        return ResourceActivitySummary(
            scope={
                "execution_id": execution_id, "workspace_id": workspace_id,
                "session_id": session_id, "turn_index": turn_index, "run_id": run_id,
            },
            reads={
                "resource_count": len({row["resource_uri"] for row in read_rows}),
                "returned_bytes": sum(row["returned_bytes"] for row in read_rows),
                **{key: modes[key] for key in ("exact", "range", "summary", "scope_only", "unknown")},
            },
            changes={
                "changed_resource_count": len(changed_resources),
                "applied": states["applied"], "proposed": states["proposed"],
                "discarded": states["discarded"],
            },
            evidence={key: evidence[key] for key in ("current", "partial", "stale", "missing", "incomplete")},
            truncated=dropped > 0,
        )

    @staticmethod
    def _json_object(raw, activity_id: str) -> dict:
        try:
            value = json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            logging.getLogger(__name__).warning(
                "Ignoring malformed resource activity metadata activity_id=%s", activity_id
            )
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _json_value(raw, activity_id: str, field: str, default, expected_type):
        valid = True
        try:
            value = json.loads(raw if raw is not None else "null")
        except (json.JSONDecodeError, TypeError):
            valid = False
            value = default
        if not isinstance(value, expected_type):
            valid = False
            value = default
        if not valid:
            logging.getLogger(__name__).warning(
                "Ignoring malformed resource activity %s activity_id=%s", field, activity_id
            )
        return value

    def list(self, *, execution_id=None, workspace_id=None, session_id=None, turn_index=None,
             run_id=None, operation=None, change_state=None, resource_uri=None, cursor=0, limit=100):
        where, params = self._scope(execution_id, workspace_id, session_id, turn_index, run_id)
        clauses = [where, "sequence>?"]
        values = [*params, int(cursor or 0)]
        for column, value in (("operation", operation), ("change_state", change_state), ("resource_uri", resource_uri)):
            if value:
                clauses.append(f"{column}=?")
                values.append(str(value))
        size = max(1, min(int(limit), 500))
        with self.database.read_transaction() as conn:
            rows = conn.execute(
                f"SELECT * FROM resource_activities WHERE {' AND '.join(clauses)} ORDER BY sequence LIMIT ?",
                (*values, size + 1),
            ).fetchall()
        has_more = len(rows) > size
        rows = rows[:size]
        return {
            "schema_version": 1, "items": [self._item(row) for row in rows],
            "next_cursor": rows[-1]["sequence"] if has_more and rows else None,
            "has_more": has_more,
        }

    @staticmethod
    def _scope(execution_id, workspace_id, session_id, turn_index, run_id=None):
        if execution_id:
            return "execution_id=?", [str(execution_id)]
        if run_id:
            return "run_id=?", [str(run_id)]
        if workspace_id and session_id and turn_index is not None:
            return "workspace_id=? AND session_id=? AND turn_index=?", [str(workspace_id), str(session_id), int(turn_index)]
        raise ValueError("execution_id or workspace/session/turn scope is required")

    @staticmethod
    def _item(row):
        keys = (
            "activity_id", "sequence", "workspace_id", "session_id", "turn_index", "execution_id",
            "slice_id", "run_id", "tool_call_id", "tool_name", "actor", "resource_uri", "operation",
            "observation_mode", "change_state", "returned_bytes", "resource_bytes", "before_digest",
            "after_digest", "before_lines", "after_lines", "evidence_status", "occurred_at",
        )
        item = {key: row[key] for key in keys}
        item["requested_range"] = SQLiteResourceActivityRepository._json_value(
            row["requested_range"], row["activity_id"], "requested_range", None, (dict, type(None))
        )
        item["observed_range"] = SQLiteResourceActivityRepository._json_value(
            row["observed_range"], row["activity_id"], "observed_range", None, (dict, type(None))
        )
        item["related_activity_ids"] = SQLiteResourceActivityRepository._json_value(
            row["related_activity_ids"], row["activity_id"], "related_activity_ids", [], list
        )
        item["metadata"] = SQLiteResourceActivityRepository._json_object(
            row["metadata"], row["activity_id"]
        )
        return item
