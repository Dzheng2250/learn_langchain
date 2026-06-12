"""Show Core daemon status."""

from src.cli.daemon import daemon_status


def register(subparsers, _config) -> None:
    """Register the Core daemon status command."""
    parser = subparsers.add_parser("status", help="show Core daemon status")
    parser.set_defaults(handler=run)


def run(args, config) -> int:
    """Print daemon health and return nonzero when Core is unavailable."""
    status = daemon_status(config)
    if status is None:
        print("Core daemon is not running.")
        return 1
    print(f"Core daemon running. uptime_ms={status['uptime_ms']}")
    return 0
