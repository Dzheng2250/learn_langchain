"""Core daemon process entry point."""

import argparse
import asyncio

from src.core.bus.server import CoreRpcServer
from src.config.settings import CORE_HOST, CORE_PORT
from src.ipc.auth import create_token, read_token, token_path


async def serve(host: str = CORE_HOST, port: int = CORE_PORT) -> None:
    token = read_token() if token_path().exists() else create_token()
    server = CoreRpcServer(token, host=host, port=port)
    await server.serve_until_shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="learn-agent-core")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve_parser = subparsers.add_parser("serve", help="run the Core daemon")
    serve_parser.add_argument("--host", default=CORE_HOST)
    serve_parser.add_argument("--port", type=int, default=CORE_PORT)
    args = parser.parse_args(argv)

    if args.command == "serve":
        asyncio.run(serve(args.host, args.port))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
