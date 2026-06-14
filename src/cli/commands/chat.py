"""Interactive and one-shot Agent chat commands."""

from src.cli.client import CoreClient
from src.cli.errors import CliError, CoreRequestError
from src.cli.render import render_agent_event, render_cli_error
from src.cli.workspace import discover_workspace_root


def register(subparsers, config) -> None:
    """Register interactive and one-shot ``chat`` CLI arguments."""
    parser = subparsers.add_parser("chat", help="chat through the Core daemon")
    parser.add_argument("message", nargs="?")
    parser.add_argument("--session", default=config.default_session_id)
    parser.add_argument("--workspace")
    parser.set_defaults(handler=run)


def run(args, config) -> int:
    """Dispatch the parsed chat command using validated CLI configuration."""
    client = CoreClient(config)
    if args.message:
        chat_once(client, args.session, args.message, args.workspace)
    else:
        interactive_chat(client, args.session, args.workspace)
    return 0


def chat_once(client: CoreClient, session_name: str, message: str, workspace: str | None = None) -> None:
    """Send one Workspace-scoped message to Core and render its event stream."""
    workspace_root = discover_workspace_root(workspace)
    print("AI: ", end="", flush=True)
    result = client.request(
        "agent.chat",
        {"workspace_root": str(workspace_root), "session_name": session_name, "message": message},
        on_event=render_agent_event,
    )
    print()
    if result.get("memory_status") == "pending" and result.get("memory_request_explicit"):
        print("Memory save has been queued and can be checked with 'learn-agent session status'.")
    if result.get("status") != "ok":
        raise CoreRequestError(result.get("error", "Agent turn failed."))


def interactive_chat(client: CoreClient, session_name: str, workspace: str | None = None) -> None:
    """Read non-empty terminal input and execute repeated one-shot turns."""
    print("Connected to Core daemon. Type 'exit' or 'quit' to stop.")
    while True:
        message = input("\nYou: ").strip()
        if not message:
            continue
        if message.lower() in {"exit", "quit"}:
            return
        try:
            chat_once(client, session_name, message, workspace)
        except CliError as exc:
            print()
            render_cli_error(exc)
