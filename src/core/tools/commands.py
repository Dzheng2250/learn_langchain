"""Workspace-bound command tools."""

import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from langchain_core.tools import tool

from src.config.settings import (
    DOCKER_CPUS,
    DOCKER_IMAGE,
    DOCKER_MEMORY,
    DOCKER_OUTPUT_LIMIT,
    DOCKER_TIMEOUT_SECONDS,
)
from src.core.tools.workspace import is_sandbox_name_excluded


def _copy_workspace(root: Path, target: Path) -> None:
    """Copy regular workspace files without following links at any depth."""

    def copy_entry(source: Path, destination: Path) -> None:
        """Recursively copy regular non-sensitive entries without following links."""
        if source.is_symlink() or is_sandbox_name_excluded(source.name):
            return
        if source.is_dir():
            destination.mkdir()
            for child in source.iterdir():
                copy_entry(child, destination / child.name)
            return
        if source.is_file():
            shutil.copy2(source, destination)

    for source in root.iterdir():
        copy_entry(source, target / source.name)


def create_run_command_in_container(root: Path):
    """Create a Docker command tool bound to a read-only Workspace copy."""
    @tool
    def run_command_in_container(command: str) -> str:
        """Run a command in an isolated read-only copy of the current workspace."""
        command = command.strip()
        if not command:
            return "Command must not be empty."
        try:
            shlex.split(command)
        except ValueError as exc:
            return f"Command parse failed: {exc}"

        with tempfile.TemporaryDirectory(prefix="agent_sandbox_") as directory:
            sandbox = Path(directory)
            _copy_workspace(root, sandbox)
            try:
                result = subprocess.run(
                    [
                        "docker", "run", "--rm", "--network", "none",
                        "--cpus", DOCKER_CPUS, "--memory", DOCKER_MEMORY,
                        "--pids-limit", "128", "--read-only", "--cap-drop", "ALL",
                        "--security-opt", "no-new-privileges", "--user", "65534:65534",
                        "--workdir", "/workspace",
                        "--mount", f"type=bind,source={sandbox},target=/workspace,readonly",
                        "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
                        DOCKER_IMAGE, "bash", "-lc", command,
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=DOCKER_TIMEOUT_SECONDS,
                    check=False,
                )
            except FileNotFoundError:
                return "Docker is not installed or is not available on PATH."
            except subprocess.TimeoutExpired:
                return f"Container command exceeded the {DOCKER_TIMEOUT_SECONDS}-second timeout."
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        output = output or f"Command completed with exit code {result.returncode} and no output."
        return output[:DOCKER_OUTPUT_LIMIT]

    return run_command_in_container
