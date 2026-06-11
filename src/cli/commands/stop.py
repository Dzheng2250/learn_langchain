"""Stop the Core daemon."""

from src.cli.daemon import stop_daemon


def register(subparsers, _config) -> None:
    parser = subparsers.add_parser("stop", help="stop the Core daemon")
    parser.set_defaults(handler=run)


def run(args, config) -> int:
    stop_daemon(config)
    print("Core daemon stopped.")
    return 0
