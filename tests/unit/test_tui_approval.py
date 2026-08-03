"""Mounted Textual tests for tool-approval dialogs."""

import unittest

from textual.app import App
from textual.widgets import Button, OptionList

from src.tui.screens.approval import (
    AcceptAllConfirmationModal,
    ApprovalCenterModal,
    ToolApprovalModal,
    approval_request_detail,
)


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


if __name__ == "__main__":
    unittest.main()
