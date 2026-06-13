"""Core daemon lifecycle helpers."""

import os
import subprocess
import sys
import time

from src.cli.client import CoreClient
from src.cli.config import CliConfig
from src.cli.errors import CoreUnavailableError, DaemonLifecycleError
from src.ipc.auth import create_token, log_path, pid_path, token_path


def daemon_status(config: CliConfig) -> dict | None:
    """Return Core health data, or ``None`` when no daemon is reachable."""
    try:
        return CoreClient(config, timeout=0.5).request("core.ping")
    except CoreUnavailableError:
        return None


def start_daemon(config: CliConfig) -> dict:
    """Start detached Core and wait until its authenticated ping succeeds."""
    status = daemon_status(config)
    if status is not None:
        return status

    try:
        config.runtime_dir.mkdir(parents=True, exist_ok=True)
        create_token(config.runtime_dir)
    except OSError as exc:
        raise DaemonLifecycleError(
            "Unable to prepare the Core daemon runtime directory.",
            hint=f"Check permissions for {config.runtime_dir}. Details: {exc}",
        ) from exc
    try:
        pid_path(config.runtime_dir).unlink(missing_ok=True)
    except OSError:
        # A stale PID file is advisory. Core will overwrite it after binding.
        pass
    creation_flags = 0
    if os.name == "nt":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    try:
        with open(log_path(config.runtime_dir), "a", encoding="utf-8") as log_file:
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "src.core",
                    "serve",
                    "--host",
                    config.core_host,
                    "--port",
                    str(config.core_port),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                close_fds=True,
                creationflags=creation_flags,
            )
    except OSError as exc:
        raise DaemonLifecycleError(
            "Unable to start the Core daemon process.",
            hint=f"Check permissions and Python environment. Details: {exc}",
        ) from exc
    deadline = time.monotonic() + config.daemon_startup_timeout_seconds
    while time.monotonic() < deadline:
        status = daemon_status(config)
        if status is not None:
            return status
        time.sleep(0.1)
    raise DaemonLifecycleError(
        "Core daemon did not become ready before the startup timeout.",
        hint=f"Check {log_path(config.runtime_dir)}.",
    )


def stop_daemon(config: CliConfig) -> dict:
    """Request graceful Core shutdown and wait for the daemon to disappear."""
    try:
        result = CoreClient(config).request("core.shutdown")
    except CoreUnavailableError:
        _cleanup_runtime_files(config)
        raise DaemonLifecycleError(
            "Core daemon is not running.",
            hint="Use 'learn-agent start' to start it.",
        )

    deadline = time.monotonic() + config.daemon_stop_timeout_seconds
    while time.monotonic() < deadline and daemon_status(config) is not None:
        time.sleep(0.1)
    if daemon_status(config) is not None:
        raise DaemonLifecycleError(
            "Core daemon did not stop before the shutdown timeout.",
            hint=f"Check {log_path(config.runtime_dir)}.",
        )
    _cleanup_runtime_files(config)
    return result


def _cleanup_runtime_files(config: CliConfig) -> None:
    """Remove advisory PID and authentication token files after shutdown."""
    for path in (pid_path(config.runtime_dir), token_path(config.runtime_dir)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
