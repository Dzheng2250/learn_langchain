"""Session pending-execution inspection and control commands."""

from src.cli.client import CoreClient
from src.cli.workspace import discover_workspace_root


def register(subparsers, _config) -> None:
    """Register explicit Session recovery commands."""
    parser = subparsers.add_parser("session", help="inspect or control a pending Session execution")
    actions = parser.add_subparsers(dest="session_action", required=True)
    for name in ("status", "discard"):
        action = actions.add_parser(name)
        action.add_argument("--session", default="default")
        action.add_argument("--workspace")
        action.set_defaults(handler=run)
    resume = actions.add_parser("resume")
    resume.add_argument("--session", default="default")
    resume.add_argument("--workspace")
    resume.add_argument("--instruction", default="")
    resume.set_defaults(handler=run)


def run(args, config) -> int:
    """Call one Session control RPC and print its structured result."""
    workspace = discover_workspace_root(args.workspace)
    client = CoreClient(config)
    params = {"workspace_root": str(workspace), "session_name": args.session}
    if args.session_action == "resume":
        params["instruction"] = args.instruction
    result = client.request(
        f"session.{args.session_action}",
        params,
        on_event=_render_notification if args.session_action == "resume" else None,
    )
    print(result)
    return 0


def _render_notification(notification: dict) -> None:
    """Render resumed token events without exposing persistence internals."""
    if notification.get("event") == "token":
        print(notification.get("data", {}).get("content", ""), end="", flush=True)
