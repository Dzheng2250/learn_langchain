"""Stop the Core daemon."""

from src.cli.daemon import stop_daemon


def register(subparsers, _config) -> None:
    """Register the graceful Core daemon shutdown command."""
    parser = subparsers.add_parser("stop", help="stop the Core daemon")
    parser.set_defaults(handler=run)


def run(args, config) -> int:
    """Request graceful shutdown and print confirmation."""
    stop_daemon(config)
    print("Core daemon stopped.")
    return 0
