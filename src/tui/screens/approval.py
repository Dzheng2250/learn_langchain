"""Textual dialogs for tool approvals and approval-mode selection."""

from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, OptionList, RadioButton, RadioSet, Static
from textual.widgets.option_list import Option


def approval_options(persistable: bool) -> tuple[tuple[str, str], ...]:
    """Return response choices in their stable keyboard/display order."""
    choices = [
        ("allow_once", "A  Allow once"),
        ("deny_once", "D  Deny once"),
    ]
    if persistable:
        choices.extend(
            (
                ("allow_session", "S  Allow for this Session"),
                ("allow_workspace", "W  Allow for this Workspace"),
                ("deny_session", "Shift+S  Deny for this Session"),
                ("deny_workspace", "Shift+W  Deny for this Workspace"),
            )
        )
    return tuple(choices)


def approval_request_detail(request: dict[str, Any]) -> str:
    """Build a defensive, content-free summary for one approval dialog."""
    args = dict(request.get("args") or {})
    for key in ("content", "old_text", "new_text"):
        value = args.get(key)
        if isinstance(value, str):
            args[key] = f"<{len(value)} chars omitted>"
    capabilities = ", ".join(
        str(item) for item in request.get("capabilities") or ()
    ) or "none"
    scope = "scoped rules available" if request.get("persistable") else "one-time only"
    reason = str(request.get("reason") or "Tool execution requires approval.")
    return (
        f"{reason}\nCapabilities: {capabilities}\n"
        f"Arguments: {json.dumps(args, ensure_ascii=False, sort_keys=True)}\n"
        f"Persistence: {scope}"
    )


class ToolApprovalModal(ModalScreen[str | None]):
    """Resolve one pending request without requiring a slash command."""

    BINDINGS = [
        Binding("a", "choose('allow_once')", "Allow once", priority=True),
        Binding("d", "choose('deny_once')", "Deny once", priority=True),
        Binding("s", "choose('allow_session')", "Allow Session", priority=True),
        Binding("w", "choose('allow_workspace')", "Allow Workspace", priority=True),
        Binding("shift+s", "choose('deny_session')", "Deny Session", priority=True),
        Binding("shift+w", "choose('deny_workspace')", "Deny Workspace", priority=True),
        Binding("escape", "dismiss_pending", "Close", priority=True),
    ]

    CSS = """
    ToolApprovalModal { align: center middle; }
    ToolApprovalModal > Vertical {
        width: 88; max-width: 92%; height: auto; max-height: 90%;
        border: round $warning; background: $surface; padding: 1 2;
    }
    ToolApprovalModal .approval-title { text-style: bold; color: $warning; }
    ToolApprovalModal .approval-detail { margin: 1 0; }
    ToolApprovalModal OptionList { height: auto; max-height: 14; }
    """

    def __init__(self, request: dict[str, Any]) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        tool = str(self.request.get("tool") or "unknown")
        detail = approval_request_detail(self.request)
        persistable = bool(self.request.get("persistable", False))
        options = [Option(label, id=response) for response, label in approval_options(persistable)]
        with Vertical():
            yield Label(f"Tool approval: {tool}", classes="approval-title")
            yield Static(detail, classes="approval-detail", markup=False)
            yield OptionList(*options, id="approval-options", markup=False)

    def on_mount(self) -> None:
        # Deliberately require an explicit choice; Enter must never imply approval.
        self.query_one("#approval-options", OptionList).highlighted = None

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))

    def action_choose(self, response: str) -> None:
        allowed = {item[0] for item in approval_options(bool(self.request.get("persistable")))}
        if response in allowed:
            self.dismiss(response)

    def action_dismiss_pending(self) -> None:
        self.dismiss(None)


class ApprovalCenterModal(ModalScreen[dict[str, str] | None]):
    """Browse pending requests and select the Session approval-mode override."""

    BINDINGS = [Binding("escape", "dismiss_center", "Close", priority=True)]

    CSS = """
    ApprovalCenterModal { align: center middle; }
    ApprovalCenterModal > Vertical {
        width: 78; max-width: 90%; height: auto;
        border: round $primary; background: $surface; padding: 1 2;
    }
    ApprovalCenterModal .center-title { text-style: bold; }
    ApprovalCenterModal .pending-list { height: auto; max-height: 12; margin-top: 1; }
    ApprovalCenterModal RadioSet { height: auto; margin: 1 0; }
    ApprovalCenterModal Horizontal { height: auto; align-horizontal: right; }
    ApprovalCenterModal Button { margin-left: 1; }
    """

    def __init__(
        self,
        mode: dict[str, Any],
        requests: tuple[dict[str, Any], ...] = (),
    ) -> None:
        super().__init__()
        self.mode = mode
        self.requests = requests

    def compose(self) -> ComposeResult:
        pending = int(self.mode.get("pending_count") or 0)
        override = self.mode.get("override_mode")
        with Vertical():
            yield Label("Tool approval center", classes="center-title")
            yield Static(f"Pending requests: {pending}", markup=False)
            if self.requests:
                yield OptionList(
                    *(
                        Option(
                            _approval_center_label(request),
                            id=str(request.get("request_id") or ""),
                        )
                        for request in self.requests
                    ),
                    id="pending-approvals",
                    classes="pending-list",
                    markup=False,
                )
            else:
                yield Static("No pending requests.", classes="pending-list", markup=False)
            with RadioSet(id="approval-mode"):
                yield RadioButton("Inherit global default", id="mode-inherit", value=override is None)
                yield RadioButton("Manual approval", id="mode-manual", value=override == "manual")
                yield RadioButton("Accept all policy requests", id="mode-accept-all", value=override == "accept_all")
            with Horizontal():
                yield Button("Review pending", id="review")
                yield Button("Apply mode", id="apply", variant="primary")
                yield Button("Close", id="close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close":
            self.dismiss(None)
            return
        if event.button.id == "review":
            if not self.requests:
                return
            pending = self.query_one("#pending-approvals", OptionList)
            option = pending.get_option_at_index(pending.highlighted or 0)
            self.dismiss({"action": "review", "request_id": str(option.id)})
            return
        radio = self.query_one("#approval-mode", RadioSet).pressed_button
        if radio is None or radio.id is None:
            return
        mode = radio.id.removeprefix("mode-").replace("-", "_")
        self.dismiss({"action": "mode", "mode": mode})

    def action_dismiss_center(self) -> None:
        self.dismiss(None)


def _approval_center_label(request: dict[str, Any]) -> str:
    """Render one content-free queue row suitable for mouse or keyboard selection."""
    tool = str(request.get("tool") or "unknown")
    reason = " ".join(str(request.get("reason") or "approval required").split())
    request_id = str(request.get("request_id") or "")
    short_id = request_id[:8] if request_id else "unknown"
    return f"{tool}  [{short_id}]  {reason}"


class AcceptAllConfirmationModal(ModalScreen[bool]):
    """Require an explicit acknowledgment before enabling automatic approval."""

    BINDINGS = [Binding("escape", "reject", "Cancel", priority=True)]

    CSS = """
    AcceptAllConfirmationModal { align: center middle; }
    AcceptAllConfirmationModal > Vertical {
        width: 76; max-width: 90%; height: auto;
        border: round $error; background: $surface; padding: 1 2;
    }
    AcceptAllConfirmationModal Horizontal { height: auto; align-horizontal: right; margin-top: 1; }
    AcceptAllConfirmationModal Button { margin-left: 1; }
    """

    def __init__(self, session_name: str = "default") -> None:
        super().__init__()
        self.session_name = session_name

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Enable accept-all mode?", classes="center-title")
            yield Static(
                f"Future ASK decisions for Session {self.session_name!r} will be "
                "allowed once automatically. "
                "Existing pending requests are unchanged, and hard security boundaries still apply.",
                markup=False,
            )
            with Horizontal():
                yield Button("Cancel", id="cancel")
                yield Button("Enable", id="confirm", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_reject(self) -> None:
        self.dismiss(False)
