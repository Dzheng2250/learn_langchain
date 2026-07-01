"""Interactive and one-shot Agent chat commands."""

from typing import Any

from src.cli.client import CoreClient
from src.cli.errors import CliError, CoreRequestError
from src.cli.render import AgentEventRenderer, render_cli_error
from src.cli.workspace import discover_workspace_root


def register(subparsers, config) -> None:
    """Register interactive and one-shot ``chat`` CLI arguments."""
    parser = subparsers.add_parser("chat", help="chat through the Core daemon")
    parser.add_argument("message", nargs="?")
    parser.add_argument("--session", default=config.default_session_id)
    parser.add_argument("--workspace")
    parser.add_argument(
        "--goal",
        action="store_true",
        help="enable private task planning for a multi-step goal",
    )
    parser.set_defaults(handler=run)


def run(args, config) -> int:
    """Dispatch the parsed chat command using validated CLI configuration."""
    client = CoreClient(config)
    if args.message:
        chat_once(client, args.session, args.message, args.workspace, goal_mode=args.goal)
    else:
        interactive_chat(client, args.session, args.workspace, goal_mode=args.goal)
    return 0


def chat_once(
    client: CoreClient,
    session_name: str,
    message: str,
    workspace: str | None = None,
    *,
    goal_mode: bool = False,
    interactive_approvals: bool = False,
) -> None:
    """Send one Workspace-scoped message to Core and render its event stream."""
    workspace_root = discover_workspace_root(workspace)
    renderer = AgentEventRenderer(goal_mode=goal_mode)
    print("AI: ", end="", flush=True)
    result = client.request(
        "agent.chat",
        {
            "workspace_root": str(workspace_root),
            "session_name": session_name,
            "message": message,
            "goal_mode": goal_mode,
        },
        on_event=renderer.render,
    )
    while (
        interactive_approvals
        and result.get("status") == "paused"
        and result.get("stop_reason") == "tool_approval"
        and renderer.pending_approval
    ):
        response = _prompt_for_tool_approval(renderer.pending_approval)
        if response is None:
            break
        request_id = renderer.pending_approval["request_id"]
        renderer.pending_approval = None
        print("\nAI: ", end="", flush=True)
        result = client.request(
            "approval.resolve",
            {
                "workspace_root": str(workspace_root),
                "session_name": session_name,
                "request_id": request_id,
                "response": response,
            },
            on_event=renderer.render,
        )
    print()
    if result.get("memory_status") == "pending" and result.get("memory_request_explicit"):
        print("Memory save has been queued and can be checked with 'learn-agent session status'.")
    if result.get("status") == "paused":
        print(result.get("message", "Agent execution paused."))
        if result.get("stop_reason") == "tool_approval":
            request = renderer.pending_approval or result.get("approval_request") or {}
            request_id = request.get("request_id", "<request_id>")
            print(
                "Resolve with 'learn-agent approval resolve "
                f"{request_id} allow_once --session {session_name}', "
                "or inspect pending requests with 'learn-agent approval list'."
            )
            return
        print(
            "Use 'learn-agent session resume --session "
            f"{session_name}' to continue, or 'learn-agent session discard --session "
            f"{session_name}' to discard it."
        )
        return
    if result.get("status") == "terminated" and result.get("auto_recovered"):
        return
    if result.get("status") != "ok":
        raise CoreRequestError(result.get("error", "Agent turn failed."))
    if (goal_mode or result.get("goal_mode")) and not renderer.done_announced:
        print("Goal mode execution completed. You can continue with a new message or exit.")


def interactive_chat(
    client: CoreClient,
    session_name: str,
    workspace: str | None = None,
    *,
    goal_mode: bool = False,
    interactive_approvals: bool = False,
) -> None:
    """Read non-empty terminal input and execute repeated one-shot turns."""
    if goal_mode:
        print("Connected to Core daemon. Goal mode enabled. Type 'exit' or 'quit' to stop.")
    else:
        print("Connected to Core daemon. Type 'exit' or 'quit' to stop.")
    while True:
        message = input("\nYou: ").strip()
        if not message:
            continue
        if message.lower() in {"exit", "quit"}:
            return
        try:
            chat_once(
                client,
                session_name,
                message,
                workspace,
                goal_mode=goal_mode,
                interactive_approvals=True,
            )
        except CliError as exc:
            print()
            render_cli_error(exc)

def _prompt_for_tool_approval(request: dict[str, Any]) -> str | None:
    """Read one explicit terminal decision without weakening policy scope."""
    tool = request.get("tool", "unknown")
    reason = request.get("reason") or "Tool policy requires confirmation."
    capabilities = ", ".join(request.get("capabilities") or []) or "unspecified"
    print(f"\nTool approval required: {tool}")
    print(f"Reason: {reason}")
    print(f"Capabilities: {capabilities}")
    choices = {
        "1": "allow_once",
        "a": "allow_once",
        "allow": "allow_once",
        "2": "deny_once",
        "d": "deny_once",
        "deny": "deny_once",
    }
    labels = ["1=allow once", "2=deny once"]
    if request.get("persistable"):
        choices.update(
            {
                "3": "allow_session",
                "as": "allow_session",
                "4": "deny_session",
                "ds": "deny_session",
                "5": "allow_workspace",
                "aw": "allow_workspace",
                "6": "deny_workspace",
                "dw": "deny_workspace",
            }
        )
        labels.extend(
            [
                "3=allow session",
                "4=deny session",
                "5=allow workspace",
                "6=deny workspace",
            ]
        )
    prompt = "Decision (" + ", ".join(labels) + ", Enter=leave pending): "
    while True:
        try:
            raw = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not raw:
            return None
        response = choices.get(raw)
        if response:
            return response
        print("Invalid approval decision.")
