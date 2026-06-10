
import os

from langchain_core.tools import tool

from src.core.common.debug import debug_print
from src.config.settings import (
    ENTIRE_FILE_MAX_LINES,
    FILE_READ_CHUNK_LINES,
    FILE_READ_OUTPUT_LIMIT,
    PARENT_FILE_READ_LINES,
    PARENT_FILE_READ_OUTPUT_LIMIT,
)


WORKSPACE_DIR = os.path.abspath(os.getcwd())
SANDBOX_EXCLUDES = {
    ".env",
    ".git",
    ".vscode",
    "__pycache__",
    ".ipynb_checkpoints",
}


def _resolve_workspace_file(path: str) -> str:
    """Resolve a workspace-relative file path without allowing escapes."""
    normalized = path.strip().replace("\\", "/").lstrip("/")
    candidate = os.path.abspath(os.path.join(WORKSPACE_DIR, normalized))

    if not candidate.startswith(WORKSPACE_DIR + os.sep) and candidate != WORKSPACE_DIR:
        raise ValueError("只能读取当前项目目录下的相对路径。")

    parts = normalized.split("/")
    if any(part in SANDBOX_EXCLUDES or part == ".." for part in parts):
        raise ValueError("该路径被安全策略拒绝。")

    if not os.path.isfile(candidate):
        raise ValueError("目标不是文件，或文件不存在。")

    return candidate


@tool
def read_workspace_file(path: str, start_line: int = 1, max_lines: int = FILE_READ_CHUNK_LINES) -> str:
    """读取已知行号范围的小片段。不要用它扫描大文件；宽泛/全文问题请用 summarize_large_file。"""
    debug_print(
        "TOOL read_workspace_file INPUT",
        f"path={path!r}, start_line={start_line!r}, max_lines={max_lines!r}",
    )

    try:
        file_path = _resolve_workspace_file(path)
    except ValueError as exc:
        result = f"读取被拒绝：{exc}"
        debug_print("TOOL read_workspace_file OUTPUT", result)
        return result

    start_line = max(1, int(start_line))
    max_lines = max(1, min(int(max_lines), FILE_READ_CHUNK_LINES))
    requested_end_line = start_line + max_lines - 1

    with open(file_path, "r", encoding="utf-8", errors="replace") as file:
        lines = file.readlines()

    total_lines = len(lines)
    selected_lines = lines[start_line - 1:requested_end_line]

    if not selected_lines:
        result = f"{path} 共有 {total_lines} 行。请求的起始行 {start_line} 超出文件范围。"
        debug_print("TOOL read_workspace_file OUTPUT", result)
        return result

    numbered_lines = []
    used_chars = 0
    last_line_number = start_line - 1

    for line_number, line in enumerate(selected_lines, start=start_line):
        formatted_line = f"{line_number}: {line.rstrip()}"
        next_used_chars = used_chars + len(formatted_line) + 1

        if numbered_lines and next_used_chars > FILE_READ_OUTPUT_LIMIT:
            break

        if not numbered_lines and next_used_chars > FILE_READ_OUTPUT_LIMIT:
            formatted_line = formatted_line[:FILE_READ_OUTPUT_LIMIT] + " ... 单行过长，已截断 ..."

        numbered_lines.append(formatted_line)
        used_chars += len(formatted_line) + 1
        last_line_number = line_number

    next_line = last_line_number + 1
    header = f"{path} 共 {total_lines} 行，当前读取 {start_line}-{last_line_number} 行。"
    remaining = total_lines - last_line_number
    if remaining > 0:
        footer = (
            f"\n\n⚠ 文件还剩 {remaining} 行未读（第 {next_line}-{total_lines} 行）。"
            "\n请使用 summarize_large_file 总结剩余内容，"
            "不要连续调用 read_workspace_file 逐块读取，这会导致超出循环次数。"
        )
    else:
        footer = "\n已到达文件末尾。"
    result = header + "\n" + "\n".join(numbered_lines) + footer

    debug_print("TOOL read_workspace_file OUTPUT", result)
    return result


@tool
def read_entire_file(path: str) -> str:
    """一次读取整个文件；仅适合 300 行以内的中小文件，大文件请用 summarize_large_file。"""
    debug_print("TOOL read_entire_file INPUT", f"path={path!r}")

    try:
        file_path = _resolve_workspace_file(path)
    except ValueError as exc:
        result = f"读取被拒绝：{exc}"
        debug_print("TOOL read_entire_file OUTPUT", result)
        return result

    with open(file_path, "r", encoding="utf-8", errors="replace") as file:
        lines = file.readlines()

    total_lines = len(lines)

    if total_lines <= ENTIRE_FILE_MAX_LINES:
        numbered = [f"{i}: {line.rstrip()}" for i, line in enumerate(lines, start=1)]
        result = f"{path} 共 {total_lines} 行，全文返回。\n" + "\n".join(numbered)
        debug_print("TOOL read_entire_file OUTPUT", result)
        return result

    preview_lines = lines[:ENTIRE_FILE_MAX_LINES]
    numbered = [f"{i}: {line.rstrip()}" for i, line in enumerate(preview_lines, start=1)]
    result = (
        f"{path} 共 {total_lines} 行，超出 {ENTIRE_FILE_MAX_LINES} 行上限，仅返回前 {ENTIRE_FILE_MAX_LINES} 行。\n"
        + "\n".join(numbered)
        + f"\n\n⚠ 文件剩余 {total_lines - ENTIRE_FILE_MAX_LINES} 行未显示。"
        "\n请使用 summarize_large_file 总结全文，不要用 read_workspace_file 逐块读取。"
    )
    debug_print("TOOL read_entire_file OUTPUT", result)
    return result


@tool
def read_workspace_file_lite(path: str, start_line: int = 1, max_lines: int = PARENT_FILE_READ_LINES) -> str:
    """父 Agent 轻量读取已知行号附近的小片段；不要连续调用，不适合搜索或总结文件。"""
    debug_print(
        "TOOL read_workspace_file_lite INPUT",
        f"path={path!r}, start_line={start_line!r}, max_lines={max_lines!r}",
    )

    try:
        file_path = _resolve_workspace_file(path)
    except ValueError as exc:
        result = f"读取被拒绝：{exc}"
        debug_print("TOOL read_workspace_file_lite OUTPUT", result)
        return result

    start_line = max(1, int(start_line))
    max_lines = max(1, min(int(max_lines), PARENT_FILE_READ_LINES))
    requested_end_line = start_line + max_lines - 1

    with open(file_path, "r", encoding="utf-8", errors="replace") as file:
        lines = file.readlines()

    total_lines = len(lines)
    selected_lines = lines[start_line - 1:requested_end_line]

    if not selected_lines:
        result = f"{path} 共有 {total_lines} 行。请求的起始行 {start_line} 超出文件范围。"
        debug_print("TOOL read_workspace_file_lite OUTPUT", result)
        return result

    numbered_lines = []
    used_chars = 0
    last_line_number = start_line - 1

    for line_number, line in enumerate(selected_lines, start=start_line):
        formatted_line = f"{line_number}: {line.rstrip()}"
        next_used_chars = used_chars + len(formatted_line) + 1

        if numbered_lines and next_used_chars > PARENT_FILE_READ_OUTPUT_LIMIT:
            break

        if not numbered_lines and next_used_chars > PARENT_FILE_READ_OUTPUT_LIMIT:
            formatted_line = formatted_line[:PARENT_FILE_READ_OUTPUT_LIMIT] + " ... 单行过长，已截断 ..."

        numbered_lines.append(formatted_line)
        used_chars += len(formatted_line) + 1
        last_line_number = line_number

    header = (
        f"{path} 共 {total_lines} 行，父 Agent 轻量读取 {start_line}-{last_line_number} 行。"
    )
    remaining = total_lines - last_line_number
    if remaining > 0:
        footer = (
            f"\n\n⚠ 文件还剩 {remaining} 行未读（第 {last_line_number + 1}-{total_lines} 行）。"
            "\n请立即使用 delegate_to_subagent 一次性读取剩余内容，"
            "不要连续调用 read_workspace_file_lite 逐块读取，这会导致超出循环次数限制。"
        )
    else:
        footer = "\n文件已全部读取完毕。"
    result = header + "\n" + "\n".join(numbered_lines) + footer

    debug_print("TOOL read_workspace_file_lite OUTPUT", result)
    return result
