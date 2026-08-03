"""Inline tool-approval controls that preserve the surrounding chat view."""

from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Static


def inline_approval_options(persistable: bool) -> tuple[str, ...]:
    """Return the deliberately small set of primary approval actions."""
    choices = ["allow_once"]
    if persistable:
        choices.append("allow_session")
    choices.append("deny_once")
    return tuple(choices)


def approval_request_detail(request: dict[str, Any]) -> str:
    """Build a defensive, content-free summary for one approval request."""
    args = dict(request.get("args") or {})
    for key in ("content", "old_text", "new_text"):
        value = args.get(key)
        if isinstance(value, str):
            args[key] = f"<{len(value)} chars omitted>"
    capabilities = ", ".join(
        str(item) for item in request.get("capabilities") or ()
    ) or "none"
    reason = str(request.get("reason") or "Tool execution requires approval.")
    return (
        f"{reason}\nCapabilities: {capabilities}  "
        f"Arguments: {json.dumps(args, ensure_ascii=False, sort_keys=True)}"
    )


class ApprovalBar(Vertical):
    """Display one pending request inline without obscuring chat history."""

    can_focus = True
    BINDINGS = [
        Binding("a", "choose('allow_once')", "Allow once", show=False, priority=True),
        Binding("s", "choose('allow_session')", "Allow Session", show=False, priority=True),
        Binding("d", "choose('deny_once')", "Deny", show=False, priority=True),
    ]
    DEFAULT_CSS = """
    ApprovalBar {
        display: none;
        height: auto;
        max-height: 7;
        border-top: solid $warning;
        background: $surface;
        padding: 0 1;
    }
    ApprovalBar .approval-title { text-style: bold; color: $warning; }
    ApprovalBar .approval-detail { height: auto; max-height: 2; color: $text-muted; }
    ApprovalBar .approval-actions { height: 3; align-horizontal: left; }
    ApprovalBar Button {
        width: auto;
        min-width: 0;
        height: 3;
        min-height: 3;
        border: none;
        padding: 0 1;
        margin: 0 1 0 0;
        background: $panel;
        color: $text;
    }
    ApprovalBar Button:hover, ApprovalBar Button:focus {
        background: $primary;
        color: $text;
        text-style: bold;
    }
    ApprovalBar #approval-allow-once { color: $success; }
    ApprovalBar #approval-allow-session { color: $accent; }
    ApprovalBar #approval-deny { color: $error; }
    ApprovalBar #approval-more { width: auto; color: $text-muted; }
    """

    class Decision(Message):
        def __init__(self, request_id: str, response: str) -> None:
            super().__init__()
            self.request_id = request_id
            self.response = response

    def __init__(self) -> None:
        super().__init__(id="approval-bar")
        self.request: dict[str, Any] | None = None

    @property
    def active_request_id(self) -> str | None:
        if self.request is None:
            return None
        return str(self.request.get("request_id") or "") or None

    def compose(self) -> ComposeResult:
        yield Static("", id="approval-title", classes="approval-title", markup=False)
        yield Static("", id="approval-detail", classes="approval-detail", markup=False)
        with Horizontal(classes="approval-actions"):
            yield Button("A  Allow once", id="approval-allow-once")
            yield Button("S  Allow in Session", id="approval-allow-session")
            yield Button("D  Deny", id="approval-deny")
            yield Static("Ctrl+Y more", id="approval-more")

    def show_request(self, request: dict[str, Any]) -> None:
        self.request = dict(request)
        tool = str(request.get("tool") or "unknown")
        self.query_one("#approval-title", Static).update(f"Approval required: {tool}")
        self.query_one("#approval-detail", Static).update(
            approval_request_detail(request)
        )
        session_button = self.query_one("#approval-allow-session", Button)
        session_button.display = bool(request.get("persistable", False))
        self.display = True
        self.focus()

    def clear_request(self, request_id: str | None = None) -> None:
        if request_id is not None and request_id != self.active_request_id:
            return
        self.request = None
        self.display = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        responses = {
            "approval-allow-once": "allow_once",
            "approval-allow-session": "allow_session",
            "approval-deny": "deny_once",
        }
        response = responses.get(event.button.id or "")
        if response is not None:
            self.action_choose(response)

    def action_choose(self, response: str) -> None:
        request_id = self.active_request_id
        if request_id is None or response not in inline_approval_options(
            bool((self.request or {}).get("persistable"))
        ):
            return
        self.post_message(self.Decision(request_id, response))
