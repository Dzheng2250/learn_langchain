"""Main chat screen — event log + input bar + command dispatch."""

from __future__ import annotations

import asyncio
from typing import Any

from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer

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
from src.tui.renderer import render_event
from src.tui.widgets.chat_log import ChatLog
from src.tui.widgets.input_bar import InputBar
from src.tui.widgets.status_bar import StatusBar


class ChatScreen(Screen):
    """Main chat screen: status bar, event log, input bar, footer."""

    BINDINGS = [
        Binding("ctrl+c", "cancel", "Cancel"),
        Binding("ctrl+d", "quit", "Quit"),
        Binding("ctrl+enter", "submit", "Send"),
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
        self._inflight_task: asyncio.Task[Any] | None = None
        self._inflight_client: AsyncCoreClient | None = None

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
        self.run_worker(self._connect_and_check(), exclusive=True, name="connect")

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
            ctx_tokens = result.get("context_tokens", 0)
            if ctx_tokens:
                status_bar = self.query_one(StatusBar)
                status_bar.set_usage(int(ctx_tokens))
        except CoreUnavailableError:
            status_bar = self.query_one(StatusBar)
            status_bar.set_disconnected("daemon not running")
        except Exception as exc:
            status_bar = self.query_one(StatusBar)
            status_bar.set_error(str(exc))
        finally:
            await client.close()

    # ── input handling ──────────────────────────────────────────────

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
        await self._handle_input(text)

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
            log.write_event("  /session <n> — switch session")
            log.write_event("  /clear       — clear the log")
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
        elif cmd == "session":
            if args:
                self._session_name = args
                bar = self.query_one(StatusBar)
                bar.set_session(args)
                self._log_note(f"Switched to session: {args}")
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

    # ── event streaming ─────────────────────────────────────────────

    async def _on_event(self, params: dict[str, Any]) -> None:
        """Callback invoked for each ``agent.event`` notification."""
        markup = render_event(params)
        if markup is None:
            return
        event = params.get("event")
        if event == "token":
            self._streamed_response_active = True
            self.query_one(ChatLog).write_token(markup)
        else:
            data = params.get("data", {})
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

    def _handle_done(self, status: str | None, data: dict[str, Any]) -> None:
        """Handle done event status and context usage."""
        ctx_tokens = data.get("context_tokens", 0)
        if ctx_tokens:
            self.query_one(StatusBar).set_usage(int(ctx_tokens))
        if status == "paused":
            self._paused_execution = True
            self.query_one(StatusBar).set_paused(True)
            # Show resume hint in the log
            log = self.query_one(ChatLog)
            log.write_event(
                "[yellow]Send /resume to continue or /discard to cancel.[/yellow]"
            )
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
        ctx_tokens = result.get("context_tokens", 0)
        if ctx_tokens:
            self.query_one(StatusBar).set_usage(int(ctx_tokens))
        summary = (
            f"[dim]{status}[/dim]  "
            f"reason: {stop_reason}  "
            f"tools: {tool_calls}  "
            f"slices: {slices}  "
            f"{dur}"
        )
        self.query_one(ChatLog).write_event(summary)

    # ── helpers ─────────────────────────────────────────────────────

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
        self.app.exit()
