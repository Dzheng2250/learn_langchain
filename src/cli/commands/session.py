"""Session pending-execution inspection and control commands."""

from src.cli.client import CoreClient
from src.cli.render import AgentEventRenderer
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
    renderer = AgentEventRenderer()
    if args.session_action == "resume":
        params["instruction"] = args.instruction
    result = client.request(
        f"session.{args.session_action}",
        params,
        on_event=renderer.render if args.session_action == "resume" else None,
    )
    if args.session_action == "resume":
        print()
        if result.get("status") == "paused":
            print(result.get("message", "Agent execution paused."))
            print(
                "Use 'learn-agent session resume --session "
                f"{args.session}' to continue, or 'learn-agent session discard --session "
                f"{args.session}' to discard it."
            )
            return 0
        if result.get("status") == "ok" and result.get("goal_mode") and not renderer.done_announced:
            print("Goal mode execution completed. You can continue with a new message or exit.")
    print(result)
    return 0
