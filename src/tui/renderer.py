"""Rich-markup event renderer for the TUI chat log.

Reuses the sanitisation and truncation semantics from ``src/cli/render.py``
but outputs Rich markup strings instead of ``print()`` calls.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Reuse constants and helpers from the CLI renderer
from rich.markup import escape

from src.cli.render import (
    ARG_FIELD_PREVIEW_LIMIT,
    ARG_PREVIEW_LIMIT,
    TASK_TOOLS,
    VISIBLE_RESULT_TOOLS,
    _preview,
    _sanitize_arg_value,
    _task_plan_lines,
    _task_update_line,
)



_TASK_PROGRESS_LINE = re.compile(r"^\[(?P<marker>[ >x-])\]\s+(?P<key>[^:]+):\s+(?P<subject>.*?)\s+\((?P<state>.*)\)$")


def is_tool_step(data: dict[str, Any]) -> bool:
    """Return whether a step event represents tool execution."""
    return data.get("type") in {"tool_call_start", "tool_call_result"}


def is_task_tool_step(data: dict[str, Any]) -> bool:
    """Return whether a step event belongs to the private task tools."""
    return is_tool_step(data) and (data.get("tool") or "") in TASK_TOOLS


def render_task_progress(data: dict[str, Any]) -> str | None:
    """Render the latest task plan as one replaceable TUI block.

    Task tool results carry the authoritative compact plan text returned by
    TaskPlanningService. The TUI treats it as state, not an append-only log, so
    every update replaces the previous visible task block.
    """
    if data.get("type") != "tool_call_result":
        return None
    tool = data.get("tool") or ""
    if tool not in TASK_TOOLS:
        return None
    content = str(data.get("content") or "").strip()
    if not content or content.startswith("Task tool error:"):
        return None

    lines = [line.rstrip() for line in content.splitlines() if line.strip()]
    task_lines = [_render_task_progress_line(line) for line in lines]
    task_lines = [line for line in task_lines if line is not None]
    if not task_lines:
        return None
    return "\n".join(["[bold cyan]● Update Todos[/bold cyan]", *task_lines])


def _render_task_progress_line(line: str) -> str | None:
    """Render one compact task-list line without treating task keys as markup."""
    if line.startswith("Task plan saved.") or line.startswith("Task updated:"):
        return None
    if line == "No private task plan exists for this Execution.":
        return "[dim]No task plan yet.[/dim]"
    match = _TASK_PROGRESS_LINE.match(line)
    if match is None:
        return f"[dim]{escape(line)}[/dim]"

    marker = match.group("marker")
    key = escape(match.group("key"))
    subject = escape(match.group("subject"))
    state = escape(match.group("state"))
    label = f"{key}: {subject}"
    if marker == "x":
        return f"[green]☑[/green] [strike dim]{label}[/strike dim]"
    if marker == ">":
        return f"[bold yellow]▣[/bold yellow] [bold]{label}[/bold] [dim]({state})[/dim]"
    if marker == "-":
        return f"[dim]☒ {label} ({state})[/dim]"
    return f"[dim]☐[/dim] {label} [dim]({state})[/dim]"

# ── event-type markers ──────────────────────────────────────────────


def render_event(params: dict[str, Any]) -> str | None:
    """Render one ``agent.event`` notification params to a Rich-markup string.

    Returns ``None`` for ``token`` events (they are accumulated in a buffer
    by the caller) or for unhandled event types.
    """
    event = params.get("event")
    data = params.get("data", {})

    if event == "token":
        # Tokens are buffered by the caller; return the raw content here
        # so the caller can accumulate them.
        return data.get("content", "")

    if event == "step":
        return _render_step(data)

    if event == "done":
        return _render_done(data)

    if event == "error":
        return _render_error(data)

    if event == "paused":
        return _render_paused(data)

    if event == "tool_approval_required":
        return (
            "[yellow bold]Tool approval required[/yellow bold]\n"
            f"[yellow]{data.get('tool', 'unknown')}[/yellow]"
        )

    if event == "model_retry_scheduled":
        return (
            "[yellow]Model request will retry: "
            f"attempt {data.get('next_attempt', '?')}/{data.get('max_attempts', '?')} "
            f"in {float(data.get('delay_seconds', 0)):.2f}s.[/yellow]"
        )

    if event == "model_attempt_invalidated":
        return (
            "The preceding model draft is incomplete and stale "
            f"({data.get('error_category', 'provider_error')})."
        )

    if event == "model_retry_exhausted":
        return (
            "[red]Model retry budget exhausted: "
            f"{data.get('error_category', 'unknown')}.[/red]"
        )

    return None


# ── step sub-types ──────────────────────────────────────────────────


def _render_step(data: dict[str, Any]) -> str:
    step_type = data.get("type", "step")
    if step_type == "agent_start":
        return "[bold blue]▶ Agent turn started.[/bold blue]"

    if step_type == "agent_message":
        content = data.get("content", "")
        return content if content else ""

    if step_type == "tool_call_start":
        return _render_tool_call_start(data)

    if step_type == "tool_call_result":
        return _render_tool_call_result(data)

    return f"[dim]▶ {step_type}[/dim]"


# ── tool call rendering ─────────────────────────────────────────────


def _render_tool_call_start(data: dict[str, Any]) -> str:
    tool = data.get("tool") or "unknown"
    args = data.get("args")
    detail = _tool_detail(tool, args)
    line = f"[bold green]▶ tool: {tool}[/bold green]"
    if detail:
        line += f"\n{dim(detail)}"
    return line


def _render_tool_call_result(data: dict[str, Any]) -> str:
    tool = data.get("tool") or "unknown"
    line = f"[green]✓ tool: {tool}[/green]"
    if tool in VISIBLE_RESULT_TOOLS:
        content = _preview(data.get("content"))
        if content:
            line += f"\n{dim(content)}"
    return line


def _tool_detail(tool: str, args: Any) -> str | None:
    """Format tool arguments for display (same logic as CLI renderer)."""
    if not isinstance(args, dict):
        return _generic_args_detail(args)
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
    return _generic_args_detail(args)


def _generic_args_detail(args: Any) -> str | None:
    """Render sanitised tool arguments (same semantics as CLI)."""
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
    preview = _preview(text, ARG_PREVIEW_LIMIT)
    return f"Args: {preview}"


# ── terminal events ─────────────────────────────────────────────────


def _render_done(data: dict[str, Any]) -> str | None:
    status = data.get("status")
    if status == "paused":
        reason = data.get("stop_reason", "paused")
        return f"[yellow]■ execution paused: {reason}[/yellow]"
    if status == "ok":
        goal_mode = data.get("goal_mode", False)
        if goal_mode:
            return "[bold green]★ goal completed[/bold green]"
        return "[green]■ completed[/green]"
    if status == "terminated" and data.get("auto_recovered"):
        # The explanation is streamed as token text. Keep done as a structural
        # marker so the TUI does not duplicate the same failure message.
        return None
    return None


def _render_error(data: dict[str, Any]) -> str:
    msg = data.get("message", "Agent turn failed.")
    return f"[red]✗ error: {msg}[/red]"


def _render_paused(data: dict[str, Any]) -> str:
    reason = data.get("stop_reason", "paused")
    return f"[yellow]■ paused: {reason}[/yellow]"


# ── helpers ─────────────────────────────────────────────────────────


def dim(text: str) -> str:
    """Wrap text in Rich dim markup."""
    return f"[dim]{text}[/dim]"
