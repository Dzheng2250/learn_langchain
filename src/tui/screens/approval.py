"""Textual dialogs for approval queue and mode management."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, OptionList, RadioButton, RadioSet, Static
from textual.widgets.option_list import Option


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
