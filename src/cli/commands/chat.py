"""Interactive and one-shot Agent chat commands."""

from src.cli.client import CoreClient
from src.cli.errors import CliError, CoreRequestError
from src.cli.render import render_agent_event, render_cli_error
from src.cli.workspace import discover_workspace_root


def register(subparsers, config) -> None:
    parser = subparsers.add_parser("chat", help="chat through the Core daemon")
    parser.add_argument("message", nargs="?")
    parser.add_argument("--session", default=config.default_session_id)
    parser.add_argument("--workspace")
    parser.set_defaults(handler=run)


def run(args, config) -> int:
    client = CoreClient(config)
    if args.message:
        chat_once(client, args.session, args.message, args.workspace)
    else:
        interactive_chat(client, args.session, args.workspace)
    return 0


def chat_once(client: CoreClient, session_name: str, message: str, workspace: str | None = None) -> None:
    workspace_root = discover_workspace_root(workspace)
    print("AI: ", end="", flush=True)
    result = client.request(
        "agent.chat",
        {"workspace_root": str(workspace_root), "session_name": session_name, "message": message},
        on_event=render_agent_event,
    )
    print()
    if result.get("status") != "ok":
        raise CoreRequestError(result.get("error", "Agent turn failed."))


def interactive_chat(client: CoreClient, session_name: str, workspace: str | None = None) -> None:
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
