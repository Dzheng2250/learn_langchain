"""Start the Core daemon."""

from src.cli.daemon import start_daemon


def register(subparsers, _config) -> None:
    parser = subparsers.add_parser("start", help="start the Core daemon")
    parser.set_defaults(handler=run)


def run(args, config) -> int:
    status = start_daemon(config)
    print(f"Core daemon running. uptime_ms={status['uptime_ms']}")
    return 0
