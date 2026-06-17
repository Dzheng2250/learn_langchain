"""Terminal rendering for Agent stream events."""

import json
from dataclasses import dataclass
from typing import Any

from src.cli.errors import CliError

DETAIL_PREVIEW_LIMIT = 2000
ARG_PREVIEW_LIMIT = 1000
ARG_FIELD_PREVIEW_LIMIT = 240
TASK_TOOLS = {"task_plan", "task_update", "task_list", "task_get"}
VISIBLE_RESULT_TOOLS = TASK_TOOLS | {"delegate_to_subagent"}
SENSITIVE_ARG_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "passwd",
    "password",
    "secret",
    "token",
)


def _preview(value: Any, limit: int = DETAIL_PREVIEW_LIMIT) -> str:
    """Return a compact single-string preview for terminal output."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else repr(value)
    if len(text) > limit:
        return text[:limit] + "\n... truncated ..."
    return text


def _is_sensitive_arg_key(key: str) -> bool:
    """Return whether an argument key should never be rendered verbatim."""
    key = key.casefold()
    return any(part in key for part in SENSITIVE_ARG_KEY_PARTS) or ".env" in key


def _sanitize_arg_value(key: str, value: Any, *, _depth: int = 0) -> Any:
    """Build a terminal-safe preview value for one tool argument."""
    if _depth > 20:
        return "[MAX_DEPTH]"
    if _is_sensitive_arg_key(key):
        return "[REDACTED]"
    if isinstance(value, str):
        return _preview(value, ARG_FIELD_PREVIEW_LIMIT)
    if isinstance(value, dict):
        return {
            str(child_key): _sanitize_arg_value(str(child_key), child_value, _depth=_depth + 1)
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [
            _sanitize_arg_value(key, item, _depth=_depth + 1)
            for item in value[:20]
        ] + (["... truncated ..."] if len(value) > 20 else [])
    return value


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

    def render(self, params: dict) -> None:
        """Render one ``agent.event`` notification without changing business state."""
        event = params.get("event")
        data = params.get("data", {})
        if event == "token":
            content = data.get("content", "")
            if content:
                self.received_token = True
                print(content, end="", flush=True)
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
        elif event == "done":
            status = data.get("status")
            if status == "paused":
                reason = data.get("stop_reason", "paused")
                print(f"\n[execution_paused: {reason}]", flush=True)
                self.done_announced = True
            elif status == "ok" and (self.goal_mode or data.get("goal_mode")):
                print("\n[goal_completed]", flush=True)
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
