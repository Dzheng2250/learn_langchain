"""Mounted Textual tests for inline and modal approval controls."""

import unittest

from textual.app import App
from textual.widgets import Button, OptionList

from src.tui.screens.approval import (
    AcceptAllConfirmationModal,
    ApprovalCenterModal,
)
from src.tui.client import safe_rpc_error_detail
from src.tui.screens.chat import ChatScreen
from src.tui.widgets.approval_bar import ApprovalBar, approval_request_detail
from src.tui.widgets.chat_log import ChatLog
from src.tui.widgets.input_bar import InputBar


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

    async def test_inline_quick_allow_posts_an_explicit_decision(self) -> None:
        results = []

        class ApprovalApp(App):
            def compose(self):
                yield ApprovalBar()

            def on_approval_bar_decision(self, event: ApprovalBar.Decision) -> None:
                results.append((event.request_id, event.response))

        app = ApprovalApp()
        async with app.run_test() as pilot:
            bar = app.query_one(ApprovalBar)
            bar.show_request({
                "request_id": "request-inline",
                "tool": "write_workspace_file",
                "reason": "Write notes.txt",
                "persistable": False,
            })
            await pilot.pause()
            self.assertTrue(bar.display)
            self.assertFalse(
                bar.query_one("#approval-allow-session", Button).display
            )

            await pilot.press("a")
            await pilot.pause()

        self.assertEqual([("request-inline", "allow_once")], results)

    async def test_inline_bar_preserves_chat_log_and_input_widgets(self) -> None:
        class MountedChatScreen(ChatScreen):
            def on_mount(self) -> None:
                pass

        app = App()
        async with app.run_test() as pilot:
            app.push_screen(MountedChatScreen())
            await pilot.pause()
            screen = app.screen
            bar = screen.query_one(ApprovalBar)
            bar.show_request({
                "request_id": "request-inline",
                "tool": "run_command_in_container",
                "persistable": True,
            })
            await pilot.pause()

            self.assertTrue(bar.display)
            self.assertTrue(screen.query_one(ChatLog).display)
            self.assertTrue(screen.query_one(InputBar).display)
            self.assertTrue(
                bar.query_one("#approval-allow-session", Button).display
            )

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
        shown = []
        screen = ChatScreen.__new__(ChatScreen)
        screen._paused_execution = True
        screen._resolving_approval_ids = {"request-old"}
        screen._pending_approval_requests = {
            "request-old": {"request_id": "request-old", "tool": "old"},
            "request-new": {"request_id": "request-new", "tool": "new"},
        }
        bar = type("Bar", (), {
            "active_request_id": None,
            "show_request": lambda _self, request: shown.append(
                request["request_id"]
            ),
        })()
        screen.query_one = lambda widget_type: bar

        ChatScreen._show_next_approval(screen)

        self.assertEqual(["request-new"], shown)

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
