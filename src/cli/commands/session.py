"""Session pending-execution inspection and control commands."""

from src.cli.client import CoreClient
from src.cli.render import AgentEventRenderer
from src.cli.workspace import discover_workspace_root


def register(subparsers, _config) -> None:
    """Register explicit Session recovery commands."""
    parser = subparsers.add_parser("session", help="inspect or control a pending Session execution")
    actions = parser.add_subparsers(dest="session_action", required=True)
    for name in ("status", "discard", "reset"):
        action = actions.add_parser(name)
        action.add_argument("--session", default="default")
        action.add_argument("--workspace")
        action.set_defaults(handler=run)
    delete = actions.add_parser("delete")
    delete.add_argument("--session", default="default")
    delete.add_argument("--workspace")
    delete.add_argument(
        "--hard",
        action="store_true",
        help="permanently delete the Session and all locally linked rows",
    )
    delete.set_defaults(handler=run)
    resume = actions.add_parser("resume")
    resume.add_argument("--session", default="default")
    resume.add_argument("--workspace")
    resume.add_argument("--instruction", default="")
    resume.add_argument(
        "--retry-conditions",
        action="store_true",
        help="retry after explicitly correcting a condition-required pause",
    )
    resume.set_defaults(handler=run)


def run(args, config) -> int:
    """Call one Session control RPC and print its structured result."""
    workspace = discover_workspace_root(args.workspace)
    client = CoreClient(config)
    params = {"workspace_root": str(workspace), "session_name": args.session}
    renderer = AgentEventRenderer()
    if args.session_action == "resume":
        params["instruction"] = args.instruction
        params["retry_conditions"] = bool(args.retry_conditions)
    elif args.session_action == "delete":
        params["hard_delete"] = bool(args.hard)
    result = client.request(
        f"session.{args.session_action}",
        params,
        on_event=renderer.render if args.session_action == "resume" else None,
    )
    if args.session_action == "resume":
        print()
        if result.get("status") == "paused":
            print(result.get("message", "Agent execution paused."))
            policy = result.get("resume_policy")
            reason = result.get("stop_reason")
            if policy == "action_required" and reason == "tool_approval":
                print("Resolve the pending request with approval.list/resolve.")
            elif policy == "action_required" and reason == "tool_recovery_required":
                print("Resolve the uncertain call with tool_recovery.list/resolve.")
            elif policy == "condition_required":
                print(
                    "Correct the blocking condition, then use 'learn-agent session resume "
                    f"--session {args.session} --retry-conditions'."
                )
            elif policy == "terminal":
                print(
                    "This execution cannot be resumed. Use 'learn-agent session discard "
                    f"--session {args.session}'."
                )
            else:
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
