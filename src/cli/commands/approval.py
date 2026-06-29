"""Inspect and resolve pending tool approvals."""

from src.cli.client import CoreClient
from src.cli.render import AgentEventRenderer
from src.cli.workspace import discover_workspace_root


def register(subparsers, _config) -> None:
    parser = subparsers.add_parser("approval", help="inspect or resolve tool approvals")
    actions = parser.add_subparsers(dest="approval_action", required=True)
    listing = actions.add_parser("list")
    resolve = actions.add_parser("resolve")
    for action in (listing, resolve):
        action.add_argument("--session", default="default")
        action.add_argument("--workspace")
        action.set_defaults(handler=run)
    resolve.add_argument("request_id")
    resolve.add_argument(
        "response",
        choices=(
            "allow_once", "allow_session", "allow_workspace",
            "deny_once", "deny_session", "deny_workspace",
        ),
    )


def run(args, config) -> int:
    workspace = discover_workspace_root(args.workspace)
    params = {"workspace_root": str(workspace), "session_name": args.session}
    renderer = AgentEventRenderer()
    if args.approval_action == "resolve":
        params.update(request_id=args.request_id, response=args.response)
    result = CoreClient(config).request(
        f"approval.{args.approval_action}",
        params,
        on_event=renderer.render if args.approval_action == "resolve" else None,
    )
    print(result)
    return 0
