"""Synchronous NDJSON JSON-RPC client used by the CLI."""

import json
import socket
from collections.abc import Callable
from uuid import uuid4

from pydantic import ValidationError

from src.cli.config import CliConfig
from src.cli.errors import (
    CliRenderError,
    CoreAuthenticationError,
    CoreClientError,
    CoreConnectionInterruptedError,
    CoreProtocolError,
    CoreRequestError,
    CoreUnavailableError,
)
from src.ipc.auth import read_token
from src.ipc.models import AgentEventNotification, JsonRpcErrorResponse, JsonRpcSuccess


class CoreClient:
    """Synchronous, single-request-per-connection Core client for CLI commands."""

    def __init__(
        self,
        config: CliConfig | None = None,
        *,
        host: str | None = None,
        port: int | None = None,
        timeout: float | None = None,
    ) -> None:
        config = config or CliConfig.load()
        self.host = host if host is not None else config.core_host
        self.port = port if port is not None else config.core_port
        self.timeout = timeout if timeout is not None else config.connect_timeout_seconds
        self.runtime_dir = config.runtime_dir

    def request(
        self,
        method: str,
        params: dict | None = None,
        on_event: Callable[[dict], None] | None = None,
    ):
        request_id = uuid4().hex
        try:
            auth_token = read_token(self.runtime_dir)
        except FileNotFoundError as exc:
            raise CoreUnavailableError(
                "Core daemon is not running or its authentication token is missing.",
                hint="Run 'learn-agent start' and try again.",
            ) from exc
        except OSError as exc:
            raise CoreUnavailableError(
                "Unable to read the Core daemon authentication token.",
                hint=f"Check permissions for {self.runtime_dir}.",
            ) from exc

        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": {"auth_token": auth_token, **(params or {})},
        }
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.settimeout(None)
                stream = sock.makefile("rwb")
                stream.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                stream.flush()
                while True:
                    line = stream.readline()
                    if not line:
                        raise CoreConnectionInterruptedError(
                            "Connection to Core was interrupted before the request completed.",
                            hint="The displayed response may be incomplete. Check daemon.log before retrying.",
                        )
                    raw = json.loads(line.decode("utf-8"))
                    if not isinstance(raw, dict):
                        raise CoreProtocolError("Core returned a non-object JSON-RPC message.")
                    if raw.get("method") == "agent.event":
                        notification = AgentEventNotification.model_validate(raw)
                        if notification.params.get("request_id") == request_id and on_event is not None:
                            try:
                                on_event(notification.params)
                            except (BrokenPipeError, OSError) as exc:
                                raise CliRenderError(
                                    "Unable to write the streamed response to the terminal."
                                ) from exc
                            except Exception as exc:
                                raise CliRenderError(
                                    "Unable to render the streamed response.",
                                    hint=f"{exc.__class__.__name__}: {exc}",
                                ) from exc
                        continue
                    if raw.get("id") != request_id:
                        continue
                    if "error" in raw:
                        response = JsonRpcErrorResponse.model_validate(raw)
                        error_type = (
                            CoreAuthenticationError
                            if response.error.code == -32001
                            else CoreRequestError
                        )
                        raise error_type(
                            f"Core rejected the request: {response.error.message} ({response.error.code}).",
                            hint="Restart Core to refresh credentials."
                            if response.error.code == -32001
                            else "",
                        )
                    return JsonRpcSuccess.model_validate(raw).result
        except CoreClientError:
            raise
        except ConnectionRefusedError as exc:
            raise CoreUnavailableError(
                f"Core daemon is not accepting connections at {self.host}:{self.port}.",
                hint="Run 'learn-agent start' and try again.",
            ) from exc
        except socket.timeout as exc:
            raise CoreUnavailableError(
                f"Timed out while connecting to Core at {self.host}:{self.port}.",
                hint="Check 'learn-agent status' and the daemon log.",
            ) from exc
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as exc:
            raise CoreConnectionInterruptedError(
                "Connection to Core was interrupted.",
                hint="The request may still be running in Core. Check daemon.log before retrying.",
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
            raise CoreProtocolError(
                "Core returned an invalid or incompatible JSON-RPC response.",
                hint="Restart Core and verify that the CLI and Core versions match.",
            ) from exc
        except OSError as exc:
            raise CoreUnavailableError(
                f"Unable to communicate with Core at {self.host}:{self.port}.",
                hint="Check 'learn-agent status' and the daemon log.",
            ) from exc
