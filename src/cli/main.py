"""CLI process entry point."""

import argparse

from src.config.environment import load_user_environment

# Keep CLI transport settings aligned with the daemon before importing modules
# that read committed defaults and environment overrides.
load_user_environment()

from src.cli.commands import register_commands
from src.cli.config import CliConfig
from src.cli.errors import CliError, ConfigurationError
from src.cli.render import render_cli_error


def main(argv: list[str] | None = None) -> int:
    try:
        config = CliConfig.load()
    except ValueError as exc:
        error = ConfigurationError(str(exc))
        render_cli_error(error)
        return error.exit_code

    parser = argparse.ArgumentParser(prog="learn-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    register_commands(subparsers, config)
    args = parser.parse_args(argv)

    try:
        return args.handler(args, config)
    except CliError as exc:
        render_cli_error(exc)
        return exc.exit_code
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except BrokenPipeError:
        return 1
    except Exception as exc:
        error = CliError(
            "The CLI encountered an unexpected error.",
            hint=f"{exc.__class__.__name__}: {exc}",
        )
        render_cli_error(error)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
