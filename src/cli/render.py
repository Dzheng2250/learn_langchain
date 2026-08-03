"""Terminal rendering for Agent stream events."""

import json
from dataclasses import dataclass
from typing import Any

from src.cli.errors import CliError
from src.core.common.redaction import sanitize_value
from src.ipc.resource_activity import resource_activity_display_stats

DETAIL_PREVIEW_LIMIT = 2000
ARG_PREVIEW_LIMIT = 1000
ARG_FIELD_PREVIEW_LIMIT = 240
TASK_TOOLS = {"task_plan", "task_update", "task_list", "task_get"}
VISIBLE_RESULT_TOOLS = TASK_TOOLS | {"delegate_to_subagent"}


def _preview(value: Any, limit: int = DETAIL_PREVIEW_LIMIT) -> str:
    """Return a compact single-string preview for terminal output."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else repr(value)
    if len(text) > limit:
        return text[:limit] + "\n... truncated ..."
    return text


def _sanitize_arg_value(key: str, value: Any, *, _depth: int = 0) -> Any:
    """Build a terminal-safe preview value for one tool argument."""
    return sanitize_value(
        value,
        key=key,
        text_limit=ARG_FIELD_PREVIEW_LIMIT,
        list_limit=20,
        depth=_depth,
    )


def _generic_tool_args_detail(args: Any) -> str | None:
    """Render default tool arguments when no tool-specific formatter exists."""
    if args in (None, {}, []):
        return None
    if isinstance(args, dict):
        safe_args = {
            str(key): _sanitize_arg_value(str(key), value)
            for key, value in args.items()
        }
    else:
        safe_args = _sanitize_arg_value("args", args)
    text = json.dumps(safe_args, ensure_ascii=False, default=str)
    return f"Args: {_preview(text, ARG_PREVIEW_LIMIT)}"


def _task_plan_lines(args: dict) -> list[str]:
    """Format task_plan arguments as a human-readable checklist."""
    tasks = args.get("tasks") if isinstance(args, dict) else None
    if not isinstance(tasks, list):
        return []
    lines = ["Task plan:"]
    for item in tasks:
        if not isinstance(item, dict):
            continue
        key = item.get("task_key", "<task>")
        subject = item.get("subject", "")
        depends_on = item.get("depends_on") or []
        dependency = f" depends_on={depends_on}" if depends_on else ""
        lines.append(f"  - {key}: {subject}{dependency}")
    return lines


def _task_update_line(args: dict) -> str:
    """Format one task_update call without dumping full notes."""
    task_key = args.get("task_key", "<task>")
    changes = []
    for field in ("status", "subject", "depends_on"):
        if args.get(field) is not None:
            changes.append(f"{field}={args[field]}")
    if args.get("notes"):
        changes.append(f"notes={_preview(args['notes'], 160)}")
    detail = ", ".join(changes) if changes else "no visible changes"
    return f"Task update: {task_key} ({detail})"


def _tool_start_detail(tool: str | None, args: Any) -> str | None:
    """Return optional detailed terminal text for selected tools."""
    if not isinstance(args, dict):
        return _generic_tool_args_detail(args)
    if tool == "task_plan":
        lines = _task_plan_lines(args)
        return "\n".join(lines) if lines else None
    if tool == "task_update":
        return _task_update_line(args)
    if tool == "task_get":
        return f"Task get: {args.get('task_key', '<task>')}"
    if tool == "delegate_to_subagent":
        objective = args.get("task") or args.get("goal") or args.get("instruction")
        return f"Delegating: {_preview(objective, 1000)}" if objective else None
    if tool == "write_workspace_file":
        return (
            f"Write: {args.get('path', '<path>')} "
            f"({len(str(args.get('content', '')).encode('utf-8'))} bytes, "
            f"overwrite={bool(args.get('overwrite'))})"
        )
    if tool == "replace_workspace_text":
        return (
            f"Replace: {args.get('path', '<path>')} "
            f"(old={len(str(args.get('old_text', '')))} chars, "
            f"new={len(str(args.get('new_text', '')))} chars, "
            f"expected={args.get('expected_count', 1)})"
        )
    if tool == "move_workspace_path":
        return (
            f"Move: {args.get('source', '<source>')} -> "
            f"{args.get('destination', '<destination>')} "
            f"(overwrite={bool(args.get('overwrite'))})"
        )
    if tool == "delete_workspace_path":
        return f"Delete: {args.get('path', '<path>')} (recursive={bool(args.get('recursive'))})"
    if tool == "create_workspace_directory":
        return f"Create directory: {args.get('path', '<path>')}"
    return _generic_tool_args_detail(args)


@dataclass
class AgentEventRenderer:
    """Render one request stream while avoiding duplicate final messages.

    Some providers expose incremental ``token`` events, while others only
    produce the completed ``step.agent_message``. The renderer accepts both:
    the completed message is a fallback only when no tokens were displayed.
    """

    goal_mode: bool = False
    received_token: bool = False
    done_announced: bool = False
    error_message: str | None = None
    current_attempt_id: str | None = None
    pending_approval: dict | None = None

    def render(self, params: dict) -> None:
        """Render one ``agent.event`` notification without changing business state."""
        event = params.get("event")
        data = params.get("data", {})
        if event == "token":
            content = data.get("content", "")
            if content:
                self.current_attempt_id = data.get("attempt_id") or self.current_attempt_id
                self.received_token = True
                print(content, end="", flush=True)
        elif event == "model_attempt_invalidated":
            attempt = data.get("attempt", "?")
            category = data.get("error_category", "provider_error")
            print(
                f"\n[model_attempt_stale: attempt {attempt}, {category}]\n"
                "The response above is incomplete and must not be used.",
                flush=True,
            )
            self.received_token = False
            self.current_attempt_id = None
        elif event == "reasoning_started":
            print("\n[thinking]", flush=True)
        elif event == "reasoning_delta":
            count = int(data.get("char_count") or 0)
            redacted = " redacted" if data.get("redacted") else " hidden"
            if data.get("content"):
                print(f"\n[thinking: {count} chars]", flush=True)
            else:
                print(f"\n[thinking: {count} chars{redacted}]", flush=True)
        elif event == "reasoning_finished":
            count = int(data.get("char_count") or 0)
            redacted = " redacted" if data.get("redacted") else " hidden"
            print(f"\n[thinking_done: {count} chars{redacted}]", flush=True)
        elif event == "tool_approval_required":
            self.pending_approval = dict(data)
            request_id = data.get("request_id", "")
            tool = data.get("tool", "unknown")
            detail = _tool_start_detail(tool, data.get("args"))
            print(f"\n[tool_approval_required: {tool}]", flush=True)
            if detail:
                print(detail, flush=True)
            print(
                "Resolve with: learn-agent approval resolve "
                f"{request_id} allow_once",
                flush=True,
            )
        elif event == "model_retry_scheduled":
            next_attempt = data.get("next_attempt", "?")
            maximum = data.get("max_attempts", "?")
            delay = float(data.get("delay_seconds", 0))
            print(
                f"\n[model_retry: attempt {next_attempt}/{maximum} in {delay:.2f}s]",
                flush=True,
            )
        elif event == "model_retry_exhausted":
            print(
                f"\n[model_retry_exhausted: {data.get('error_category', 'unknown')}]",
                flush=True,
            )
        elif event == "goal_continuation_started":
            print("\n[goal_continuation: checking unfinished tasks]", flush=True)
        elif event == "step":
            step_type = data.get("type", "step")
            if step_type == "agent_message" and not self.received_token:
                print(data.get("content", ""), end="", flush=True)
            elif step_type == "tool_call_start":
                tool = data.get("tool") or ""
                print(f"\n[{step_type}: {tool}]", flush=True)
                detail = _tool_start_detail(tool, data.get("args"))
                if detail:
                    print(detail, flush=True)
            elif step_type == "tool_call_result":
                tool = data.get("tool") or ""
                print(f"\n[{step_type}: {tool}]", flush=True)
                if tool in VISIBLE_RESULT_TOOLS:
                    content = _preview(data.get("content"))
                    if content:
                        print(content, flush=True)
        elif event == "resource_activity_summary":
            stats = resource_activity_display_stats(data.get("summary"))
            print(
                f"\n[resources: read {stats['resource_count']} resource(s), "
                f"{stats['returned_bytes']} bytes; "
                f"changed {stats['changed_resource_count']}; warnings {stats['warnings']}]",
                flush=True,
            )
        elif event == "done":
            status = data.get("status")
            if status == "paused":
                reason = data.get("stop_reason", "paused")
                print(f"\n[execution_paused: {reason}]", flush=True)
                self.done_announced = True
            elif status == "ok" and (self.goal_mode or data.get("goal_mode")):
                print("\n[goal_completed]", flush=True)
                self.done_announced = True
            elif status == "terminated" and data.get("auto_recovered"):
                # The user-facing explanation is streamed as token text. The
                # done payload keeps structured fields for TUI/debug clients.
                self.done_announced = True
        elif event == "error":
            # The final JSON-RPC response carries the same failure. Record it
            # here but render once from chat_once to avoid duplicate terminal
            # errors when a stream ends with an error event.
            self.error_message = data.get("message", "Agent turn failed.")


def render_agent_event(params: dict) -> None:
    """Render a standalone event; request streams should use AgentEventRenderer."""
    AgentEventRenderer().render(params)


def render_cli_error(error: CliError) -> None:
    """Render one expected CLI failure without a traceback."""
    print(f"Error: {error.message}")
    if error.hint:
        print(f"Hint: {error.hint}")
