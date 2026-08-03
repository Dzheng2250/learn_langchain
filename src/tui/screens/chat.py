"""Main chat screen — event log + input bar + command dispatch."""

from __future__ import annotations

import asyncio
from typing import Any

from textual import events
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer
from rich.markup import escape

from src.cli.workspace import discover_workspace_root
from src.ipc.auth import read_token
from src.tui.client import (
    AsyncCoreClient,
    CoreAuthenticationError,
    CoreConnectionInterruptedError,
    CoreRequestError,
    CoreUnavailableError,
)
from src.tui.config import TuiConfig
from src.tui.screens.approval import (
    AcceptAllConfirmationModal,
    ApprovalCenterModal,
    ToolApprovalModal,
)
from src.tui.renderer import (
    is_task_tool_step,
    is_tool_step,
    render_event,
    render_task_progress,
)
from src.tui.widgets.chat_log import ChatLog
from src.tui.widgets.input_bar import InputBar
from src.tui.widgets.status_bar import StatusBar


class ChatScreen(Screen):
    """Main chat screen: status bar, event log, input bar, footer."""

    BINDINGS = [
        Binding("ctrl+c", "cancel", "Cancel"),
        Binding("ctrl+d", "quit", "Quit"),
        Binding("ctrl+enter", "submit", "Send"),
        Binding("ctrl+o", "toggle_tool_events", "Tools"),
        Binding("ctrl+t", "toggle_reasoning", "Thinking"),
        Binding("ctrl+y", "approval_center", "Approvals"),
        Binding("pageup", "log_page_up", "Log Page Up", show=False),
        Binding("pagedown", "log_page_down", "Log Page Down", show=False),
        Binding("home", "log_home", "Log Home", show=False),
        Binding("end", "log_end", "Log End", show=False),
    ]

    CSS = """
    Screen {
        layout: vertical;
    }
    StatusBar {
        height: 1;
        dock: top;
        background: $panel;
        color: $text;
        padding: 0 1;
    }
    ChatLog {
        height: 1fr;
    }
    InputBar {
        height: 3;
        dock: bottom;
        border: solid $primary;
    }
    Footer {
        height: 1;
    }
    """

    def __init__(self, config: TuiConfig | None = None) -> None:
        super().__init__()
        self._config = config or TuiConfig()
        self._client: AsyncCoreClient | None = None
        self._session_name = self._config.default_session
        self._goal_mode = False
        self._paused_execution = False
        self._busy = False  # True while a chat/resume request is in flight
        self._auth_token = ""
        self._workspace_root = ""
        self._streamed_response_active = False
        self._show_tool_events = False
        self._inflight_task: asyncio.Task[Any] | None = None
        self._inflight_client: AsyncCoreClient | None = None
        self._event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=2000)
        self._event_worker_task: asyncio.Task[Any] | None = None
        self._input_task: asyncio.Task[Any] | None = None
        self._pending_approval_ids: set[str] = set()
        self._pending_approval_requests: dict[str, dict[str, Any]] = {}
        self._resolving_approval_ids: set[str] = set()
        self._approval_modal_open = False
        self._active_approval_request_id: str | None = None

    def compose(self):
        yield StatusBar()
        yield ChatLog()
        yield InputBar()
        yield Footer()

    def on_mount(self) -> None:
        self._load_auth()
        self._resolve_workspace()
        status_bar = self.query_one(StatusBar)
        status_bar.set_connecting(self._config.core_host, self._config.core_port)
        status_bar.set_session(self._session_name)
        self._ensure_event_worker()
        self.run_worker(self._connect_and_check(), exclusive=True, name="connect")

    def on_unmount(self) -> None:
        """Stop the queued event renderer when the screen is removed."""
        task = self._event_worker_task
        if task is not None and not task.done():
            task.cancel()
        input_task = self._input_task
        if input_task is not None and not input_task.done():
            input_task.cancel()

    # ── auth & workspace ───────────────────────────────────────────

    def _load_auth(self) -> None:
        try:
            self._auth_token = read_token(self._config.runtime_dir)
        except Exception as exc:
            self._log_error(f"Cannot read auth token: {exc}")
            self._auth_token = ""

    def _resolve_workspace(self) -> None:
        self._workspace_root = str(discover_workspace_root(None))

    # ── connection lifecycle ────────────────────────────────────────

    async def _connect_and_check(self) -> None:
        """Connect, ping, check session status, then idle."""
        client = AsyncCoreClient(
            self._config.core_host,
            self._config.core_port,
            timeout=self._config.connect_timeout,
        )
        status_bar = self.query_one(StatusBar)

        try:
            await client.connect()
            await client.ping(self._auth_token)
        except CoreUnavailableError:
            status_bar.set_disconnected("daemon not running")
            return
        except Exception as exc:
            status_bar.set_error(str(exc))
            return
        finally:
            await client.close()

        self._client = client
        status_bar.set_connected(self._config.core_host, self._config.core_port)

        # Check for paused execution
        await self._check_session_status()

    async def _ensure_connected(self) -> AsyncCoreClient:
        """Return a connected client; try once if not already connected."""
        client = AsyncCoreClient(
            self._config.core_host,
            self._config.core_port,
            timeout=self._config.connect_timeout,
        )
        try:
            await client.connect()
            await client.ping(self._auth_token)
        except CoreUnavailableError:
            await client.close()
            raise
        self._client = client
        return client

    async def _check_session_status(self) -> None:
        """Check if the current session has a paused execution."""
        if not self._auth_token:
            return
        client = AsyncCoreClient(
            self._config.core_host,
            self._config.core_port,
            timeout=self._config.connect_timeout,
        )
        try:
            await client.connect()
            result = await client.request(
                "session.status",
                {
                    "auth_token": self._auth_token,
                    "workspace_root": self._workspace_root,
                    "session_name": self._session_name,
                },
            )
            pending = result.get("pending_execution")
            if pending is not None:
                self._paused_execution = True
                status_bar = self.query_one(StatusBar)
                status_bar.set_paused(True)
                log = self.query_one(ChatLog)
                log.write_event(
                    "[yellow]● Session has a paused execution — "
                    "send /resume to continue or /discard to cancel.[/yellow]"
                )
            else:
                self._paused_execution = False
                self.query_one(StatusBar).set_paused(False)
            self._update_context_usage(result)
            approval = result.get("tool_approval") or {}
            self.query_one(StatusBar).set_approval_mode(
                str(approval.get("effective_mode") or "manual")
            )
        except CoreUnavailableError:
            status_bar = self.query_one(StatusBar)
            status_bar.set_disconnected("daemon not running")
        except Exception as exc:
            status_bar = self.query_one(StatusBar)
            status_bar.set_error(str(exc))
        finally:
            await client.close()

    # ── input handling ──────────────────────────────────────────────

    def on_mouse_scroll_up(self, _event: events.MouseScrollUp) -> None:
        """Route wheel-up events outside the log to the chat history."""
        log = self.query_one(ChatLog)
        log.pause_auto_scroll()
        log.scroll_up(animate=False)

    def on_mouse_scroll_down(self, _event: events.MouseScrollDown) -> None:
        """Route wheel-down events outside the log to the chat history."""
        log = self.query_one(ChatLog)
        log.scroll_down(animate=False)
        try:
            log.call_after_refresh(log._resume_if_at_bottom)
        except Exception:
            log._resume_if_at_bottom()

    def action_toggle_tool_events(self) -> None:
        """Toggle verbose tool execution events without adding log noise."""
        self._show_tool_events = not self._show_tool_events
        self.query_one(ChatLog).set_tool_events_visible(self._show_tool_events)

    def action_toggle_reasoning(self) -> None:
        """Expand or collapse the latest model thinking block."""
        self.query_one(ChatLog).toggle_reasoning()

    async def action_submit(self) -> None:
        """Submit the current input (Ctrl+Enter)."""
        bar = self.query_one(InputBar)
        if self._busy:
            self._log_note("Busy — wait for the current request to finish.")
            return
        text = bar.text.strip()
        if not text:
            return
        bar.text = ""
        self._start_input_task(text)

    def _start_input_task(self, text: str) -> None:
        """Start command/chat handling without blocking Textual message dispatch."""
        task = asyncio.create_task(self._run_input_task(text))
        self._input_task = task

    async def _run_input_task(self, text: str) -> None:
        """Run one submitted input and surface unexpected failures in the log."""
        try:
            await self._handle_input(text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log_error(f"TUI input handling failed: {exc}")

    async def _handle_input(self, text: str) -> None:
        """Dispatch user input: command or chat message."""
        if text.startswith("/"):
            await self._dispatch_command(text)
        else:
            await self._send_chat(text)

    async def _dispatch_command(self, text: str) -> None:
        """Dispatch a ``/`` command."""
        parts = text[1:].split(None, 1)
        cmd = parts[0].lower() if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "help":
            log = self.query_one(ChatLog)
            log.write_event("[bold]Commands:[/bold]")
            log.write_event("  /help        — show this help")
            log.write_event("  /goal <msg>  — send a goal-mode message")
            log.write_event("  /resume      — resume paused execution")
            log.write_event("  /discard     — discard paused execution")
            log.write_event("  /approvals   — list pending tool approvals")
            log.write_event("  /approve [request_id] <response> — resolve an approval")
            log.write_event("  /approval-mode [inherit|manual|accept_all --ack]")
            log.write_event("  /session <n> — switch session")
            log.write_event("  /clear       — clear the log")
            log.write_event("  Ctrl+O       — show/hide tool details")
            log.write_event("  Ctrl+Y       — open the approval center")
            log.write_event("  Ctrl+C       — cancel")
            log.write_event("  Ctrl+D       — quit")
        elif cmd == "goal":
            if not args:
                self._log_note("Usage: /goal <message>")
                return
            self._goal_mode = True
            bar = self.query_one(StatusBar)
            bar.set_goal_mode(True)
            await self._send_chat(args, goal_mode=True)
        elif cmd == "resume":
            await self._resume_execution(args)
        elif cmd == "discard":
            await self._discard_execution()
        elif cmd == "approvals":
            await self._list_approvals()
        elif cmd == "approve":
            await self._resolve_approval(args or "allow_once")
        elif cmd == "approval-mode":
            await self._approval_mode_command(args)
        elif cmd == "session":
            if args:
                self._session_name = args
                pending_ids = getattr(self, "_pending_approval_ids", None)
                if pending_ids is not None:
                    pending_ids.clear()
                pending_requests = getattr(self, "_pending_approval_requests", None)
                if pending_requests is not None:
                    pending_requests.clear()
                resolving_ids = getattr(self, "_resolving_approval_ids", None)
                if resolving_ids is not None:
                    resolving_ids.clear()
                self._active_approval_request_id = None
                bar = self.query_one(StatusBar)
                bar.set_session(args)
                self._log_note(f"Switched to session: {args}")
                await self._check_session_status()
            else:
                self._log_note(f"Current session: {self._session_name}")
        elif cmd == "clear":
            log = self.query_one(ChatLog)
            log.clear()
        else:
            self._log_note(f"Unknown command: /{cmd}. Try /help.")

    # ── core actions ────────────────────────────────────────────────

    async def _send_chat(
        self,
        message: str,
        goal_mode: bool = False,
    ) -> None:
        """Send ``agent.chat`` and stream events."""
        if not self._auth_token:
            self._log_note("Auth token not available — cannot send.")
            return

        self._busy = True
        current_task = asyncio.current_task()
        log = self.query_one(ChatLog)
        log.force_scroll_to_bottom()
        log.reset_task_progress()
        status_bar = self.query_one(StatusBar)
        mode_tag = " [bold cyan]goal[/bold cyan]" if goal_mode else ""
        log.write_event(f"[bold]▶ sending{ mode_tag }[/bold]")
        log.write_event(f"[bold bright_white]You:[/bold bright_white] {message}")

        client = AsyncCoreClient(
            self._config.core_host,
            self._config.core_port,
            timeout=self._config.request_timeout,
        )
        self._inflight_task = current_task
        self._inflight_client = client
        try:
            await client.connect()
        except CoreUnavailableError:
            status_bar.set_disconnected("daemon not running")
            self._clear_inflight(current_task, client)
            return

        try:
            result = await client.request(
                "agent.chat",
                {
                    "auth_token": self._auth_token,
                    "workspace_root": self._workspace_root,
                    "session_name": self._session_name,
                    "message": message,
                    "goal_mode": goal_mode,
                },
                on_event=self._on_event,
            )
        except CoreAuthenticationError:
            log.write_event("[red]Authentication failed. Restart the daemon.[/red]")
        except CoreConnectionInterruptedError:
            log.write_event("[red]Connection lost mid-stream.[/red]")
        except CoreRequestError as exc:
            log.write_event(f"[red]Request failed: {exc}[/red]")
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.write_event(f"[red]Unexpected error: {exc}[/red]")
        else:
            await self._wait_for_event_queue()
            self._handle_result(result)
        finally:
            await client.close()
            self._clear_inflight(current_task, client)

    async def _resume_execution(self, instruction: str = "") -> None:
        """Resume a paused execution."""
        if not self._auth_token:
            self._log_note("Auth token not available.")
            return

        self._busy = True
        current_task = asyncio.current_task()
        log = self.query_one(ChatLog)
        log.force_scroll_to_bottom()
        log.write_event("[bold]▶ resuming execution[/bold]")

        client = AsyncCoreClient(
            self._config.core_host,
            self._config.core_port,
            timeout=self._config.request_timeout,
        )
        self._inflight_task = current_task
        self._inflight_client = client
        try:
            await client.connect()
        except CoreUnavailableError:
            self.query_one(StatusBar).set_disconnected("daemon not running")
            self._clear_inflight(current_task, client)
            return

        try:
            result = await client.request(
                "session.resume",
                {
                    "auth_token": self._auth_token,
                    "workspace_root": self._workspace_root,
                    "session_name": self._session_name,
                    "instruction": instruction,
                },
                on_event=self._on_event,
            )
        except CoreAuthenticationError:
            log.write_event("[red]Authentication failed.[/red]")
        except CoreConnectionInterruptedError:
            log.write_event("[red]Connection lost mid-stream.[/red]")
        except CoreRequestError as exc:
            log.write_event(f"[red]Resume failed: {exc}[/red]")
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.write_event(f"[red]Unexpected error: {exc}[/red]")
        else:
            await self._wait_for_event_queue()
            self._handle_result(result)
        finally:
            await client.close()
            self._clear_inflight(current_task, client)

    async def _discard_execution(self) -> None:
        """Discard a paused execution."""
        if not self._auth_token:
            self._log_note("Auth token not available.")
            return

        log = self.query_one(ChatLog)
        client = AsyncCoreClient(
            self._config.core_host,
            self._config.core_port,
            timeout=self._config.connect_timeout,
        )
        try:
            await client.connect()
            result = await client.request(
                "session.discard",
                {
                    "auth_token": self._auth_token,
                    "workspace_root": self._workspace_root,
                    "session_name": self._session_name,
                },
            )
            log.write_event(f"[yellow]Execution discarded: {result.get('status')}[/yellow]")
            self._paused_execution = False
            self.query_one(StatusBar).set_paused(False)
        except Exception as exc:
            log.write_event(f"[red]Discard failed: {exc}[/red]")
        finally:
            await client.close()

    async def _list_approvals(self) -> None:
        client = AsyncCoreClient(
            self._config.core_host, self._config.core_port,
            timeout=self._config.connect_timeout,
        )
        try:
            await client.connect()
            result = await client.request(
                "approval.list",
                {
                    "auth_token": self._auth_token,
                    "workspace_root": self._workspace_root,
                    "session_name": self._session_name,
                },
            )
            requests = result.get("requests", [])
            self._pending_approval_ids = {
                str(request["request_id"]) for request in requests
            }
            self._pending_approval_requests = {
                str(request["request_id"]): dict(request) for request in requests
            }
            if not requests:
                self._log_note("No pending tool approvals.")
            for request in requests:
                self.query_one(ChatLog).write_event(
                    f"[yellow]Approval {escape(request['request_id'])}: "
                    f"{escape(request['tool'])} {escape(str(request.get('args', {})))}[/yellow]"
                )
            if len(requests) > 1:
                self._log_note(
                    "Multiple approvals are pending. Use "
                    "/approve <request_id> <response>."
                )
        finally:
            await client.close()

    async def _get_approval_mode(self) -> dict[str, Any]:
        client = AsyncCoreClient(
            self._config.core_host, self._config.core_port,
            timeout=self._config.connect_timeout,
        )
        try:
            await client.connect()
            result = await client.request(
                "approval.mode.get",
                {
                    "auth_token": self._auth_token,
                    "workspace_root": self._workspace_root,
                    "session_name": self._session_name,
                },
            )
            self.query_one(StatusBar).set_approval_mode(
                str(result.get("effective_mode") or "manual")
            )
            return result
        finally:
            await client.close()

    async def _set_approval_mode(
        self,
        mode: str,
        *,
        acknowledge_risk: bool = False,
    ) -> dict[str, Any]:
        client = AsyncCoreClient(
            self._config.core_host, self._config.core_port,
            timeout=self._config.connect_timeout,
        )
        try:
            await client.connect()
            result = await client.request(
                "approval.mode.set",
                {
                    "auth_token": self._auth_token,
                    "workspace_root": self._workspace_root,
                    "session_name": self._session_name,
                    "mode": mode,
                    "acknowledge_risk": acknowledge_risk,
                },
            )
            effective = str(result.get("effective_mode") or "manual")
            self.query_one(StatusBar).set_approval_mode(effective)
            pending_note = " Existing pending requests are unchanged." if result.get("existing_pending_unchanged") else ""
            self._log_note(f"Tool approval mode: {effective}.{pending_note}")
            return result
        finally:
            await client.close()

    async def _approval_mode_command(self, arguments: str) -> None:
        parts = arguments.lower().split()
        if not parts:
            result = await self._get_approval_mode()
            self._log_note(
                "Tool approval mode: "
                f"{result.get('effective_mode', 'manual')} "
                f"(override={result.get('override_mode') or 'inherit'}, "
                f"pending={result.get('pending_count', 0)})."
            )
            return
        mode = parts[0]
        if mode not in {"inherit", "manual", "accept_all"}:
            self._log_note(
                "Usage: /approval-mode [inherit|manual|accept_all --ack]"
            )
            return
        acknowledged = "--ack" in parts[1:]
        if mode == "accept_all" and not acknowledged:
            self._log_note(
                "accept_all requires explicit risk acknowledgment: "
                "/approval-mode accept_all --ack"
            )
            return
        await self._set_approval_mode(mode, acknowledge_risk=acknowledged)

    async def _resolve_approval(self, arguments: str) -> None:
        allowed = {
            "allow_once", "allow_session", "allow_workspace",
            "deny_once", "deny_session", "deny_workspace",
        }
        parts = arguments.split()
        request_id = None
        response = parts[-1] if parts else ""
        if len(parts) == 2:
            request_id = parts[0]
        elif len(parts) != 1:
            self._log_note("Usage: /approve [request_id] <response>")
            return
        if response not in allowed:
            self._log_note("Approval response must be allow_once, allow_session, allow_workspace, deny_once, deny_session, or deny_workspace.")
            return
        if request_id is None:
            if not self._pending_approval_ids:
                await self._list_approvals()
            if len(self._pending_approval_ids) > 1:
                self._log_note(
                    "Multiple approvals are pending. Use "
                    "/approve <request_id> <response>."
                )
                return
            request_id = next(iter(self._pending_approval_ids), None)
        if not request_id:
            return
        resolving_ids = getattr(self, "_resolving_approval_ids", None)
        if resolving_ids is None:
            resolving_ids = set()
            self._resolving_approval_ids = resolving_ids
        if request_id in resolving_ids:
            self._log_note("This approval request is already being resolved.")
            return
        resolving_ids.add(request_id)
        self._busy = True
        current_task = asyncio.current_task()
        client = AsyncCoreClient(
            self._config.core_host, self._config.core_port,
            timeout=self._config.request_timeout,
        )
        self._inflight_task = current_task
        self._inflight_client = client
        try:
            await client.connect()
            result = await client.request(
                "approval.resolve",
                {
                    "auth_token": self._auth_token,
                    "workspace_root": self._workspace_root,
                    "session_name": self._session_name,
                    "request_id": request_id,
                    "response": response,
                },
                on_event=self._on_event,
            )
            await self._wait_for_event_queue()
            self._pending_approval_ids.discard(request_id)
            self._pending_approval_requests.pop(request_id, None)
            self._handle_result(result)
        except CoreAuthenticationError:
            self._log_error("Authentication failed while resolving approval.")
        except CoreConnectionInterruptedError:
            self._log_error("Connection lost while resuming the approved tool call.")
        except CoreRequestError as exc:
            self._log_error(f"Approval failed: {exc}")
            await self._list_approvals()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log_error(f"Unexpected approval error: {exc}")
        finally:
            resolving_ids.discard(request_id)
            await client.close()
            self._clear_inflight(current_task, client)

    def _show_next_approval(self, request_id: str | None = None) -> None:
        """Open one approval dialog only after Core confirmed a paused turn."""
        if self._approval_modal_open or not self._paused_execution:
            return
        requests = getattr(self, "_pending_approval_requests", {})
        resolving = getattr(self, "_resolving_approval_ids", set())
        request = (
            requests.get(request_id)
            if request_id and request_id not in resolving
            else None
        )
        if request is None:
            request = next(
                (
                    item
                    for item in requests.values()
                    if str(item.get("request_id") or "") not in resolving
                ),
                None,
            )
        if request is None:
            return
        request_id = str(request.get("request_id") or "")
        if not request_id:
            return
        self._approval_modal_open = True
        self._active_approval_request_id = request_id
        self.app.push_screen(ToolApprovalModal(request), self._approval_modal_result)

    def _approval_modal_result(self, response: str | None) -> None:
        self._approval_modal_open = False
        request_id = self._active_approval_request_id
        self._active_approval_request_id = None
        if response is None:
            return
        if request_id is None:
            return
        self.run_worker(
            self._resolve_approval(f"{request_id} {response}"),
            exclusive=False,
            name="resolve-tool-approval",
        )

    def action_approval_center(self) -> None:
        """Open the reusable pending-approval and mode-management dialog."""
        self.run_worker(
            self._open_approval_center(),
            exclusive=False,
            name="approval-center",
        )

    async def _open_approval_center(self) -> None:
        try:
            await self._list_approvals()
            mode = await self._get_approval_mode()
        except Exception as exc:
            self._log_error(f"Cannot open approval center: {exc}")
            return
        resolving = getattr(self, "_resolving_approval_ids", set())
        requests = tuple(
            request
            for request in self._pending_approval_requests.values()
            if str(request.get("request_id") or "") not in resolving
        )
        self.app.push_screen(
            ApprovalCenterModal(mode, requests),
            self._approval_center_result,
        )

    def _approval_center_result(self, result: dict[str, str] | None) -> None:
        if not result:
            return
        if result.get("action") == "review":
            self._show_next_approval(result.get("request_id"))
            return
        mode = result.get("mode")
        if mode == "accept_all":
            self.app.push_screen(
                AcceptAllConfirmationModal(self._session_name),
                self._accept_all_confirmation_result,
            )
            return
        if mode in {"inherit", "manual"}:
            self.run_worker(
                self._set_approval_mode(mode),
                exclusive=False,
                name="set-approval-mode",
            )

    def _accept_all_confirmation_result(self, confirmed: bool) -> None:
        if not confirmed:
            return
        self.run_worker(
            self._set_approval_mode("accept_all", acknowledge_risk=True),
            exclusive=False,
            name="set-approval-mode",
        )

    # ── event streaming ─────────────────────────────────────────────

    async def _on_event(self, params: dict[str, Any]) -> None:
        """Queue ``agent.event`` notifications so IPC bursts do not drive UI directly."""
        queue = getattr(self, "_event_queue", None)
        if queue is None:
            await self._render_event(params)
            return
        self._ensure_event_worker()
        await queue.put(params)
        await self._yield_to_textual()

    def _ensure_event_worker(self) -> None:
        """Start the background UI event consumer for real mounted screens."""
        if not hasattr(self, "_event_queue"):
            return
        task = self._event_worker_task
        if task is None or task.done():
            self._event_worker_task = asyncio.create_task(self._drain_event_queue())

    async def _drain_event_queue(self) -> None:
        """Render queued Core events outside the socket read loop."""
        while True:
            params = await self._event_queue.get()
            try:
                if params is None:
                    return
                try:
                    await self._render_event(params)
                except Exception as exc:
                    self._log_event_render_failure(exc)
            finally:
                self._event_queue.task_done()
            await self._yield_to_textual()

    def _log_event_render_failure(self, exc: Exception) -> None:
        """Report event-render failures without killing the queue consumer."""
        try:
            self.query_one(ChatLog).write_event(f"[red]TUI event render failed: {exc}[/red]")
        except Exception:
            pass

    async def _wait_for_event_queue(self) -> None:
        """Wait until all Core notifications already received by TUI are rendered."""
        queue = getattr(self, "_event_queue", None)
        if queue is not None:
            await queue.join()

    async def _render_event(self, params: dict[str, Any]) -> None:
        """Render one already-queued ``agent.event`` notification."""
        event = params.get("event")
        if event == "model_attempt_invalidated":
            data = params.get("data", {})
            self.query_one(ChatLog).mark_tokens_stale(
                "The preceding model draft is incomplete and stale "
                f"({data.get('error_category', 'provider_error')})."
            )
            self._streamed_response_active = False
            return
        data = params.get("data", {})
        if event == "tool_approval_required":
            request_id = str(data.get("request_id") or "")
            if request_id:
                self._pending_approval_ids.add(request_id)
                requests = getattr(self, "_pending_approval_requests", None)
                if requests is None:
                    requests = {}
                    self._pending_approval_requests = requests
                requests[request_id] = dict(data)
            safe_detail = render_event(params) or "[yellow]Tool approval required[/yellow]"
            self.query_one(ChatLog).write_event(
                f"{safe_detail}\n[dim]Use A/D in the approval dialog or Ctrl+Y to review.[/dim]"
            )
            return
        if event == "reasoning_started":
            self.query_one(ChatLog).start_reasoning(
                expanded=bool(data.get("expanded", False)),
                display=str(data.get("display") or "metadata"),
            )
            return
        if event == "reasoning_delta":
            self.query_one(ChatLog).append_reasoning(
                str(data.get("content") or ""),
                char_count=int(data.get("char_count") or 0),
                redacted=bool(data.get("redacted", False)),
            )
            return
        if event == "reasoning_finished":
            self.query_one(ChatLog).finish_reasoning(
                char_count=int(data.get("char_count") or 0),
                redacted=bool(data.get("redacted", False)),
            )
            return
        if event == "step" and is_task_tool_step(data):
            progress = render_task_progress(data)
            if progress is not None:
                self.query_one(ChatLog).write_task_progress(progress)

        markup = render_event(params)
        if markup is None:
            return
        if event == "token":
            self._streamed_response_active = True
            self.query_one(ChatLog).write_token(markup)
            return

        if event == "step" and is_tool_step(data):
            self.query_one(ChatLog).write_tool_event(markup)
            self._streamed_response_active = False
            return

        if (
            event == "step"
            and data.get("type") == "agent_message"
            and self._streamed_response_active
        ):
            self.query_one(ChatLog).flush_tokens()
            self._streamed_response_active = False
            return
        log = self.query_one(ChatLog)
        log.write_event(markup)
        self._streamed_response_active = False
        if event == "done":
            status = data.get("status")
            self._handle_done(status, data)

    async def _yield_to_textual(self) -> None:
        """Let Textual process scroll, mouse, and keyboard events between stream frames."""
        await asyncio.sleep(0.001)

    def _handle_done(self, status: str | None, data: dict[str, Any]) -> None:
        """Handle done event status and context usage."""
        self._update_context_usage(data)
        if status == "paused":
            self._paused_execution = True
            self.query_one(StatusBar).set_paused(True)
            # Show resume hint in the log
            log = self.query_one(ChatLog)
            log.write_event(
                "[yellow]Send /resume to continue or /discard to cancel.[/yellow]"
            )
            self.call_after_refresh(self._show_next_approval)
        elif status == "ok":
            if self._paused_execution:
                self._paused_execution = False
                self.query_one(StatusBar).set_paused(False)
            if self._goal_mode:
                self._goal_mode = False
                self.query_one(StatusBar).set_goal_mode(False)

    # ── result handling ─────────────────────────────────────────────

    def _handle_result(self, result: dict[str, Any]) -> None:
        """Render the final RPC result summary."""
        status = result.get("status", "unknown")
        stop_reason = result.get("stop_reason", "")
        tool_calls = result.get("tool_call_count", 0)
        slices = result.get("slices_used", 0)
        dur = result.get("durability", "")
        self._update_context_usage(result)
        summary = (
            f"[dim]{status}[/dim]  "
            f"reason: {stop_reason}  "
            f"tools: {tool_calls}  "
            f"slices: {slices}  "
            f"{dur}"
        )
        self.query_one(ChatLog).write_event(summary)
        if status == "paused" and stop_reason == "tool_approval":
            self._paused_execution = True
            self.query_one(StatusBar).set_paused(True)
            self.call_after_refresh(self._show_next_approval)

    # ── helpers ─────────────────────────────────────────────────────


    def _update_context_usage(self, payload: dict[str, Any]) -> None:
        """Update status-bar context usage when Core reports a snapshot.

        ``context_tokens`` may legitimately be 0 after reset or archive. Checking
        truthiness would leave stale usage visible, so only field presence matters.
        """
        if "context_tokens" not in payload:
            return
        try:
            context_tokens = int(payload.get("context_tokens") or 0)
        except (TypeError, ValueError):
            return
        self.query_one(StatusBar).set_usage(context_tokens)

    def _log_error(self, message: str) -> None:
        self.query_one(ChatLog).write_event(f"[red]{message}[/red]")

    def _log_note(self, message: str) -> None:
        self.query_one(ChatLog).write_event(f"[dim]{message}[/dim]")

    def _clear_inflight(
        self,
        task: asyncio.Task[Any] | None,
        client: AsyncCoreClient,
    ) -> None:
        """Clear request state only if it still belongs to this request."""
        if self._inflight_task is task and self._inflight_client is client:
            self._inflight_task = None
            self._inflight_client = None
            self._busy = False
            self._streamed_response_active = False

    # ── key actions ─────────────────────────────────────────────────

    def action_log_page_up(self) -> None:
        """Scroll chat history upward regardless of input focus."""
        log = self.query_one(ChatLog)
        log.pause_auto_scroll()
        log.scroll_page_up(animate=False)

    def action_log_page_down(self) -> None:
        """Scroll chat history downward and resume following at the bottom."""
        log = self.query_one(ChatLog)
        log.scroll_page_down(animate=False)
        try:
            log.call_after_refresh(log._resume_if_at_bottom)
        except Exception:
            log._resume_if_at_bottom()

    def action_log_home(self) -> None:
        """Jump to the beginning of chat history."""
        log = self.query_one(ChatLog)
        log.pause_auto_scroll()
        log.scroll_home(animate=False)

    def action_log_end(self) -> None:
        """Jump to latest chat output and resume follow-tail."""
        self.query_one(ChatLog).force_scroll_to_bottom()

    def action_cancel(self) -> None:
        """Cancel the current operation (Ctrl+C)."""
        if not self._busy:
            self._log_note("Nothing to cancel.")
            return

        task = self._inflight_task
        client = self._inflight_client
        self._inflight_task = None
        self._inflight_client = None
        self._busy = False
        self._streamed_response_active = False
        self._pending_approval_ids.clear()
        requests = getattr(self, "_pending_approval_requests", None)
        if requests is not None:
            requests.clear()
        resolving_ids = getattr(self, "_resolving_approval_ids", None)
        if resolving_ids is not None:
            resolving_ids.clear()
        self._active_approval_request_id = None

        if task is not None and not task.done():
            task.cancel()
        if client is not None:
            self.run_worker(client.close(), exclusive=False, name="cancel-rpc")

        self._log_note(
            "Cancelled current request. The Core turn may continue briefly after "
            "the RPC connection is closed."
        )

    def action_quit(self) -> None:
        """Quit the TUI (Ctrl+D)."""
        task = getattr(self, "_event_worker_task", None)
        if task is not None and not task.done():
            task.cancel()
        self.app.exit()
