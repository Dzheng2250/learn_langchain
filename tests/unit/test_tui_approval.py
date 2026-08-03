"""Mounted Textual tests for tool-approval dialogs."""

import unittest
from unittest.mock import PropertyMock, patch

from textual.app import App
from textual.widgets import Button, OptionList

from src.tui.screens.approval import (
    AcceptAllConfirmationModal,
    ApprovalCenterModal,
    ToolApprovalModal,
    approval_request_detail,
)
from src.tui.client import safe_rpc_error_detail
from src.tui.screens.chat import ChatScreen


class TuiApprovalModalTest(unittest.IsolatedAsyncioTestCase):
    def test_approval_detail_omits_write_content(self) -> None:
        detail = approval_request_detail({
            "reason": "Write a file",
            "args": {"path": "notes.txt", "content": "private-body"},
            "capabilities": ["file_write"],
            "persistable": True,
        })

        self.assertIn("notes.txt", detail)
        self.assertIn("file_write", detail)
        self.assertNotIn("private-body", detail)

    async def test_quick_allow_requires_an_explicit_key(self) -> None:
        results = []
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(
                ToolApprovalModal({
                    "tool": "write_workspace_file",
                    "reason": "Write notes.txt",
                    "persistable": False,
                }),
                results.append,
            )
            await pilot.pause()
            options = app.screen.query_one("#approval-options", OptionList)
            self.assertIsNone(options.highlighted)
            self.assertEqual(2, options.option_count)

            await pilot.press("a")
            await pilot.pause()

        self.assertEqual(["allow_once"], results)

    async def test_all_dialogs_mount_with_textual_css(self) -> None:
        app = App()
        async with app.run_test() as pilot:
            screens = (
                ApprovalCenterModal({
                    "pending_count": 2,
                    "override_mode": "manual",
                }),
                AcceptAllConfirmationModal(),
            )
            for screen in screens:
                app.push_screen(screen)
                await pilot.pause()
                app.pop_screen()
                await pilot.pause()

    async def test_approval_center_lists_and_selects_a_pending_request(self) -> None:
        results = []
        app = App()
        requests = (
            {
                "request_id": "request-first",
                "tool": "write_workspace_file",
                "reason": "Write notes.txt",
            },
            {
                "request_id": "request-second",
                "tool": "delete_workspace_path",
                "reason": "Delete old.txt",
            },
        )
        async with app.run_test() as pilot:
            app.push_screen(
                ApprovalCenterModal(
                    {"pending_count": 2, "override_mode": "manual"},
                    requests,
                ),
                results.append,
            )
            await pilot.pause()
            pending = app.screen.query_one("#pending-approvals", OptionList)
            self.assertEqual(2, pending.option_count)
            pending.highlighted = 1
            app.screen.query_one("#review", Button).press()
            await pilot.pause()

        self.assertEqual(
            [{"action": "review", "request_id": "request-second"}],
            results,
        )

    def test_next_dialog_skips_request_currently_being_resolved(self) -> None:
        pushed = []
        screen = ChatScreen.__new__(ChatScreen)
        screen._approval_modal_open = False
        screen._paused_execution = True
        screen._resolving_approval_ids = {"request-old"}
        screen._pending_approval_requests = {
            "request-old": {"request_id": "request-old", "tool": "old"},
            "request-new": {"request_id": "request-new", "tool": "new"},
        }
        fake_app = type("App", (), {
            "push_screen": lambda _self, modal, callback: pushed.append(
                (modal.request["request_id"], callback)
            )
        })()

        with patch.object(
            ChatScreen,
            "app",
            new_callable=PropertyMock,
            return_value=fake_app,
        ):
            ChatScreen._show_next_approval(screen)

        self.assertEqual("request-new", screen._active_approval_request_id)
        self.assertEqual("request-new", pushed[0][0])

    def test_rpc_error_detail_excludes_rejected_input_values(self) -> None:
        detail = safe_rpc_error_detail([
            {
                "loc": ["response"],
                "msg": "Input should be 'allow_once'",
                "input": "secret-value",
            }
        ])

        self.assertEqual("response: Input should be 'allow_once'", detail)
        self.assertNotIn("secret-value", detail)


if __name__ == "__main__":
    unittest.main()
