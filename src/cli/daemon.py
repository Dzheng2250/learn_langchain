"""Core daemon lifecycle helpers."""

import os
import signal
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


def stop_daemon(config: CliConfig, *, force: bool = False) -> dict:
    """Request graceful Core shutdown, optionally terminating a stuck daemon.

    A stuck synchronous tool can prevent the Core process from finishing its
    normal cleanup. ``force=True`` is the explicit operator escape hatch for
    that case; the default path still preserves graceful shutdown semantics.
    """
    try:
        result = CoreClient(config).request("core.shutdown")
    except CoreUnavailableError:
        # HTTP is already down, but the process may still be alive.
        if force:
            pid = _read_daemon_pid(config)
            if pid is not None and _pid_is_running(pid):
                if _terminate_pid(pid, config.daemon_stop_timeout_seconds):
                    _cleanup_runtime_files(config)
                    return {"status": "forced_stopped"}
                _cleanup_runtime_files(config)
                raise DaemonLifecycleError(
                    "Force-stop failed: daemon process did not terminate.",
                    hint=(
                        f"PID {pid} could not be killed after SIGTERM and SIGKILL. "
                        f"Check {log_path(config.runtime_dir)}."
                    ),
                )
        _cleanup_runtime_files(config)
        raise DaemonLifecycleError(
            "Core daemon is not running.",
            hint="Use 'learn-agent start' to start it.",
        )

    deadline = time.monotonic() + config.daemon_stop_timeout_seconds
    while time.monotonic() < deadline and daemon_status(config) is not None:
        time.sleep(0.1)

    if daemon_status(config) is not None:
        # Daemon HTTP is still responsive — graceful shutdown timed out
        if force:
            pid = _read_daemon_pid(config)
            if pid is None:
                raise DaemonLifecycleError(
                    "Cannot force-stop daemon: advisory PID file not found.",
                    hint=(
                        f"The daemon is still responding but no PID file was found at "
                        f"{pid_path(config.runtime_dir)}. Check {log_path(config.runtime_dir)} "
                        "for the daemon process."
                    ),
                )
            if _terminate_pid(pid, config.daemon_stop_timeout_seconds):
                _cleanup_runtime_files(config)
                return {"status": "forced_stopped", "graceful_result": result}
            raise DaemonLifecycleError(
                "Force-stop failed: daemon process did not terminate.",
                hint=(
                    f"Checked PID {pid} from {pid_path(config.runtime_dir)} — "
                    "the process could not be killed after SIGTERM and SIGKILL. "
                    "Check system logs and consider manual termination."
                ),
            )
        raise DaemonLifecycleError(
            "Core daemon did not stop before the shutdown timeout.",
            hint=(
                f"Check {log_path(config.runtime_dir)}. If a tool is stuck and "
                "you accept interrupting the daemon process, run "
                "'learn-agent stop --force'."
            ),
        )

    # HTTP server is down — verify the process actually exited
    daemon_pid = _read_daemon_pid(config)
    if daemon_pid is not None and _pid_is_running(daemon_pid):
        if force:
            if _terminate_pid(daemon_pid, config.daemon_stop_timeout_seconds):
                _cleanup_runtime_files(config)
                return {"status": "forced_stopped", "graceful_result": result}
            raise DaemonLifecycleError(
                "Force-stop failed: daemon process did not terminate.",
                hint=(
                    f"The process (PID {daemon_pid}) could not be killed after "
                    "SIGTERM and SIGKILL. Check system logs."
                ),
            )
        raise DaemonLifecycleError(
            "Core daemon HTTP server stopped, but the process is still running.",
            hint=(
                f"The daemon process (PID {daemon_pid}) may be stuck in a system "
                "call. Run 'learn-agent stop --force' to terminate it."
            ),
        )

    _cleanup_runtime_files(config)
    return result


def _read_daemon_pid(config: CliConfig) -> int | None:
    """Read the advisory daemon PID, returning ``None`` for stale data."""
    try:
        return int(pid_path(config.runtime_dir).read_text(encoding="ascii").strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def _pid_is_running(pid: int) -> bool:
    """Return whether a process ID still appears to be alive."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _terminate_pid(pid: int, timeout_seconds: float) -> bool:
    """Terminate one process ID and wait briefly for it to exit."""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return not _pid_is_running(pid)

    deadline = time.monotonic() + max(0.5, min(timeout_seconds, 5.0))
    while time.monotonic() < deadline:
        if not _pid_is_running(pid):
            return True
        time.sleep(0.1)

    sigkill = getattr(signal, "SIGKILL", None)
    if sigkill is not None:
        try:
            os.kill(pid, sigkill)
        except OSError:
            return not _pid_is_running(pid)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if not _pid_is_running(pid):
                return True
            time.sleep(0.1)
    return not _pid_is_running(pid)


def _cleanup_runtime_files(config: CliConfig) -> None:
    """Remove advisory PID and authentication token files after shutdown."""
    for path in (pid_path(config.runtime_dir), token_path(config.runtime_dir)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
