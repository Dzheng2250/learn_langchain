"""Stop the Core daemon."""

from src.cli.daemon import stop_daemon


def register(subparsers, _config) -> None:
    """Register the graceful Core daemon shutdown command."""
    parser = subparsers.add_parser("stop", help="stop the Core daemon")
    parser.add_argument(
        "--force",
        action="store_true",
        help="terminate the daemon process if graceful shutdown times out",
    )
    parser.set_defaults(handler=run)


def run(args, config) -> int:
    """Request graceful shutdown and print confirmation."""
    result = stop_daemon(config, force=args.force)
    if result.get("status") == "forced_stopped":
        print("Core daemon force-stopped.")
    else:
        print("Core daemon stopped.")
    return 0
