import os
import shlex
import subprocess

from langchain_core.tools import tool

from agent_debug import debug_print


BASH_PATH = os.getenv("BASH_PATH", "bash")
WORKSPACE_DIR = os.path.abspath(os.getcwd())


@tool
def get_weather(city: str) -> str:
    """根据城市名称查询当前天气。"""
    debug_print("TOOL get_weather INPUT", f"city={city!r}")

    # 这里先用本地假数据模拟真实天气 API，后续可以替换成 HTTP 请求。
    weather_data = {
        "北京": "晴，18°C，北风 2 级",
        "上海": "多云，22°C，东南风 3 级",
        "深圳": "小雨，26°C，湿度较高",
        "香港": "阴，25°C，偶有阵雨",
    }
    result = weather_data.get(city, f"暂时没有 {city} 的天气数据。")

    debug_print("TOOL get_weather OUTPUT", result)
    return result


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


# 统一导出工具列表，主流程只需要导入 tools。
tools = [get_weather, run_bash_command]
