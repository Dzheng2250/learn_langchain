
import os
import shlex
import shutil
import subprocess
import tempfile

from langchain_core.tools import tool

from src.core.common.debug import debug_print
from src.config.settings import (
    BASH_PATH,
    DOCKER_CPUS,
    DOCKER_IMAGE,
    DOCKER_MEMORY,
    DOCKER_OUTPUT_LIMIT,
    DOCKER_TIMEOUT_SECONDS,
)
from src.core.hooks.events import record_command_failed, record_command_finished, record_command_started
from src.core.tools.workspace import SANDBOX_EXCLUDES, WORKSPACE_DIR


def _copy_sanitized_workspace(target_dir: str) -> None:
    """Copy workspace files to a temporary directory without local secrets."""
    for name in os.listdir(WORKSPACE_DIR):
        if name in SANDBOX_EXCLUDES or name.endswith(".pyc"):
            continue

        source_path = os.path.join(WORKSPACE_DIR, name)
        target_path = os.path.join(target_dir, name)

        if os.path.isdir(source_path):
            shutil.copytree(
                source_path,
                target_path,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".env",
                    "__pycache__",
                    ".ipynb_checkpoints",
                    "*.pyc",
                ),
            )
        else:
            shutil.copy2(source_path, target_path)


def _format_command_result(command: str, result: subprocess.CompletedProcess) -> str:
    """Format command output and trim very long results."""
    output = result.stdout.strip()
    error = result.stderr.strip()
    detail = "\n".join(part for part in (output, error) if part)

    if not detail:
        detail = "命令执行完成，但没有输出。"

    if len(detail) > DOCKER_OUTPUT_LIMIT:
        detail = detail[:DOCKER_OUTPUT_LIMIT] + "\n... 输出已截断 ..."

    if result.returncode != 0:
        return f"命令执行失败，退出码 {result.returncode}。\n{detail}"

    return detail


@tool
def run_bash_command(command: str) -> str:
    """执行安全的本地 Bash 只读命令，例如 pwd、ls、date、whoami。"""
    debug_print("TOOL run_bash_command INPUT", f"command={command!r}")

    # 不要把任意 shell 权限直接交给模型；这里限制为少量只读命令。
    allowed_prefixes = (
        "pwd",
        "ls",
        "date",
        "whoami",
        "echo",
        "python --version",
        "python -V",
    )
    blocked_tokens = (";", "&", "|", ">", "<", "`", "$", "\n", "\r")

    command = command.strip()
    if not command:
        result = "命令不能为空。"
        debug_print("TOOL run_bash_command OUTPUT", result)
        return result

    if any(token in command for token in blocked_tokens):
        result = "命令被拒绝：不允许使用 shell 控制符、管道、重定向或变量展开。"
        debug_print("TOOL run_bash_command OUTPUT", result)
        return result

    if not any(command == prefix or command.startswith(f"{prefix} ") for prefix in allowed_prefixes):
        result = f"命令被拒绝：当前只允许这些只读命令：{', '.join(allowed_prefixes)}"
        debug_print("TOOL run_bash_command OUTPUT", result)
        return result

    try:
        command_parts = shlex.split(command)
    except ValueError as exc:
        result = f"命令解析失败：{exc}"
        debug_print("TOOL run_bash_command OUTPUT", result)
        return result

    for part in command_parts[1:]:
        if part.startswith("-"):
            continue
        if (
            part.startswith(("~", "/", "\\"))
            or (len(part) >= 2 and part[1] == ":")
            or ".." in part.replace("\\", "/").split("/")
        ):
            result = "命令被拒绝：只能访问当前项目目录下的相对路径。"
            debug_print("TOOL run_bash_command OUTPUT", result)
            return result

    try:
        result = subprocess.run(
            [BASH_PATH, "-lc", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=5,
            cwd=WORKSPACE_DIR,
            check=False,
        )
    except FileNotFoundError:
        result = f"找不到 Bash 可执行文件：{BASH_PATH}。可以在 .env 中设置 BASH_PATH。"
        debug_print("TOOL run_bash_command OUTPUT", result)
        return result
    except subprocess.TimeoutExpired:
        result = "命令执行超时。"
        debug_print("TOOL run_bash_command OUTPUT", result)
        return result

    output = result.stdout.strip()
    error = result.stderr.strip()

    if result.returncode != 0:
        detail = "\n".join(part for part in (output, error) if part)
        result_text = f"命令执行失败，退出码 {result.returncode}。\n{detail}"
        debug_print("TOOL run_bash_command OUTPUT", result_text)
        return result_text

    result_text = output or "命令执行成功，但没有输出。"
    debug_print("TOOL run_bash_command OUTPUT", result_text)
    return result_text


@tool
def run_command_in_container(command: str) -> str:
    """在隔离 Docker 容器中执行只读命令，适合查看项目文件或运行安全检查。"""
    debug_print("TOOL run_command_in_container INPUT", f"command={command!r}")
    record_command_started(
        "agent_tools",
        command=command,
        message="Container command requested.",
    )

    command = command.strip()
    if not command:
        result = "命令不能为空。"
        debug_print("TOOL run_command_in_container OUTPUT", result)
        record_command_failed(
            "agent_tools",
            reason="empty_command",
            message="Container command rejected.",
            level="warning",
        )
        return result

    try:
        shlex.split(command)
    except ValueError as exc:
        result = f"命令解析失败：{exc}"
        debug_print("TOOL run_command_in_container OUTPUT", result)
        record_command_failed(
            "agent_tools",
            reason="parse_error",
            command=command,
            detail=str(exc),
            message="Container command parse failed.",
            level="warning",
        )
        return result

    try:
        docker_check = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=5,
            check=False,
        )
    except FileNotFoundError:
        result = "找不到 docker 命令。请先安装并启动 Docker Desktop。"
        debug_print("TOOL run_command_in_container OUTPUT", result)
        record_command_failed(
            "agent_tools",
            reason="docker_not_found",
            command=command,
            message="Docker command was not found.",
            level="error",
        )
        return result
    except subprocess.TimeoutExpired:
        result = "检查 Docker 状态超时。请确认 Docker Desktop 已启动。"
        debug_print("TOOL run_command_in_container OUTPUT", result)
        record_command_failed(
            "agent_tools",
            reason="docker_check_timeout",
            command=command,
            message="Docker status check timed out.",
            level="error",
        )
        return result

    if docker_check.returncode != 0:
        detail = (docker_check.stderr or docker_check.stdout).strip()
        result = f"Docker 不可用。请确认 Docker Desktop 已启动。\n{detail}"
        debug_print("TOOL run_command_in_container OUTPUT", result)
        record_command_failed(
            "agent_tools",
            reason="docker_unavailable",
            command=command,
            returncode=docker_check.returncode,
            detail=detail,
            message="Docker is unavailable.",
            level="error",
        )
        return result

    with tempfile.TemporaryDirectory(prefix="agent_sandbox_") as sandbox_dir:
        _copy_sanitized_workspace(sandbox_dir)

        docker_command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--cpus",
            DOCKER_CPUS,
            "--memory",
            DOCKER_MEMORY,
            "--pids-limit",
            "128",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65534:65534",
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,source={sandbox_dir},target=/workspace,readonly",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            DOCKER_IMAGE,
            "bash",
            "-lc",
            command,
        ]

        try:
            result = subprocess.run(
                docker_command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=DOCKER_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            output = f"容器命令执行超时，超过 {DOCKER_TIMEOUT_SECONDS} 秒。"
            debug_print("TOOL run_command_in_container OUTPUT", output)
            record_command_failed(
                "agent_tools",
                reason="container_timeout",
                command=command,
                detail=f"timeout_seconds={DOCKER_TIMEOUT_SECONDS}",
                message="Container command timed out.",
                level="error",
            )
            return output

    output = _format_command_result(command, result)
    debug_print("TOOL run_command_in_container OUTPUT", output)
    if result.returncode == 0:
        record_command_finished(
            "agent_tools",
            returncode=result.returncode,
            output=output,
            message="Container command finished.",
        )
    else:
        record_command_failed(
            "agent_tools",
            reason="nonzero_exit",
            command=command,
            returncode=result.returncode,
            detail=output,
            message="Container command finished with non-zero exit code.",
            level="warning",
        )
    return output
