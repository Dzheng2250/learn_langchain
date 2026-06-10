"""Deprecated compatibility entry point for the CLI client."""

import sys

from src.cli.main import main


def compatibility_main(argv: list[str] | None = None) -> int:
    """Forward legacy execution to the installed CLI behavior."""
    print(
        "Notice: agent_loop.py is a compatibility entry point; prefer 'learn-agent'.",
        file=sys.stderr,
    )
    arguments = list(sys.argv[1:] if argv is None else argv)
    return main(arguments or ["chat"])


if __name__ == "__main__":
    raise SystemExit(compatibility_main())
