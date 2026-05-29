import os
import shlex
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from agent_config import (
    BASH_PATH,
    DOCKER_CPUS,
    DOCKER_IMAGE,
    DOCKER_MEMORY,
    DOCKER_OUTPUT_LIMIT,
    DOCKER_TIMEOUT_SECONDS,
    FILE_READ_CHUNK_LINES,
    FILE_READ_OUTPUT_LIMIT,
    LARGE_FILE_CHUNK_LINES,
    LARGE_FILE_MAP_WORKERS,
    LARGE_FILE_MAX_CHUNKS,
    LARGE_FILE_SUMMARY_LIMIT,
    MODEL,
    PARENT_FILE_READ_LINES,
    PARENT_FILE_READ_OUTPUT_LIMIT,
)
from agent_debug import debug_print
from agent_skills import LocalSkillStore


WORKSPACE_DIR = os.path.abspath(os.getcwd())
skill_store = LocalSkillStore(WORKSPACE_DIR)
load_dotenv()
SANDBOX_EXCLUDES = {
    ".env",
    ".git",
    ".vscode",
    "__pycache__",
    ".ipynb_checkpoints",
}


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


def _chunk_numbered_lines(lines: list[str], chunk_size: int) -> list[tuple[int, int, str]]:
    """Split file lines into numbered text chunks."""
    chunks = []
    for start_index in range(0, len(lines), chunk_size):
        end_index = min(start_index + chunk_size, len(lines))
        numbered_lines = [
            f"{line_number}: {line.rstrip()}"
            for line_number, line in enumerate(lines[start_index:end_index], start=start_index + 1)
        ]
        chunks.append((start_index + 1, end_index, "\n".join(numbered_lines)))
    return chunks


def _format_chunk_ranges(chunks: list[tuple[int, int, str]]) -> str:
    """Format chunk line ranges without including file content."""
    return ", ".join(f"{start}-{end}" for start, end, _text in chunks)


def _create_summary_llm() -> ChatOpenAI:
    """Create an internal non-streaming LLM for map-reduce file summaries."""
    return ChatOpenAI(
        model=MODEL,
        api_key=os.getenv("ALIYUN_API_KEY"),
        base_url=os.getenv("ALIYUN_BASE_URL"),
        temperature=0,
        streaming=False,
    )


def _summarize_large_file_chunk(path: str, question: str, chunk: tuple[int, int, str]) -> tuple[int, str]:
    """Summarize one file chunk for the map step."""
    start_line, end_line, chunk_text = chunk
    debug_print(
        "TOOL summarize_large_file MAP READ",
        f"path={path!r}, lines={start_line}-{end_line}",
    )

    llm = _create_summary_llm()
    response = llm.invoke([
        SystemMessage(
            content=(
                "You are a map step in a map-reduce file summarizer. "
                "Extract facts from this chunk that answer the user's question. "
                "Keep important line references. If this chunk is irrelevant, reply exactly: IRRELEVANT."
            )
        ),
        HumanMessage(
            content=(
                f"File: {path}\n"
                f"Question: {question}\n"
                f"Chunk lines: {start_line}-{end_line}\n\n"
                f"{chunk_text}"
            )
        ),
    ])

    content = response.content.strip()
    if not content or content == "IRRELEVANT":
        debug_print(
            "TOOL summarize_large_file MAP SUMMARY",
            f"lines={start_line}-{end_line}\nIRRELEVANT",
        )
        return start_line, ""

    note = f"Lines {start_line}-{end_line}:\n{content}"
    debug_print("TOOL summarize_large_file MAP SUMMARY", note)
    return start_line, note


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
    footer = (
        f"\n下一段可调用 read_workspace_file(path={path!r}, start_line={next_line})"
        if next_line <= total_lines
        else "\n已到达文件末尾。"
    )
    result = header + "\n" + "\n".join(numbered_lines) + footer

    debug_print("TOOL read_workspace_file OUTPUT", result)
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
    footer = (
        "\n如果还需要继续读取更多内容，请改用 delegate_to_subagent，"
        "不要连续调用 read_workspace_file_lite。"
    )
    result = header + "\n" + "\n".join(numbered_lines) + footer

    debug_print("TOOL read_workspace_file_lite OUTPUT", result)
    return result


@tool
def list_skills() -> str:
    """List available local skills under the configured skills directory."""
    debug_print("TOOL list_skills INPUT", f"skills_dir={skill_store.skills_dir!r}")
    result = skill_store.format_skill_list()
    debug_print("TOOL list_skills OUTPUT", result)
    return result


@tool
def read_skill(skill_name: str) -> str:
    """Read a specific local skill's SKILL.md file by skill directory name."""
    debug_print("TOOL read_skill INPUT", f"skill_name={skill_name!r}")

    try:
        result = skill_store.read_skill(skill_name)
    except ValueError as exc:
        result = f"Skill read rejected: {exc}"
        debug_print("TOOL read_skill OUTPUT", result)
        return result

    debug_print("TOOL read_skill OUTPUT", result)
    return result


@tool
def summarize_large_file(path: str, question: str) -> str:
    """用于宽泛问题、全文总结、跨章节搜索或未知行号的大文件阅读"""
    debug_print("TOOL summarize_large_file INPUT", f"path={path!r}, question={question!r}")

    try:
        file_path = _resolve_workspace_file(path)
    except ValueError as exc:
        result = f"总结被拒绝：{exc}"
        debug_print("TOOL summarize_large_file OUTPUT", result)
        return result

    with open(file_path, "r", encoding="utf-8", errors="replace") as file:
        lines = file.readlines()

    chunks = _chunk_numbered_lines(lines, LARGE_FILE_CHUNK_LINES)
    total_chunks = len(chunks)
    chunks_to_read = chunks[:LARGE_FILE_MAX_CHUNKS]

    if not chunks_to_read:
        result = f"{path} 是空文件。"
        debug_print("TOOL summarize_large_file OUTPUT", result)
        return result

    map_results = []
    max_workers = max(1, min(LARGE_FILE_MAP_WORKERS, len(chunks_to_read)))
    debug_print(
        "TOOL summarize_large_file READ PLAN",
        "\n".join([
            f"path={path!r}",
            f"total_lines={len(lines)}",
            f"chunk_lines={LARGE_FILE_CHUNK_LINES}",
            f"chunks_read={len(chunks_to_read)} of {total_chunks}",
            f"max_workers={max_workers}",
            f"line_ranges={_format_chunk_ranges(chunks_to_read)}",
        ]),
    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(_summarize_large_file_chunk, path, question, chunk)
            for chunk in chunks_to_read
        ]

        for future in as_completed(futures):
            try:
                map_results.append(future.result())
            except Exception as exc:
                failed_note = f"Map chunk failed: {exc}"
                debug_print("TOOL summarize_large_file MAP SUMMARY", failed_note)
                map_results.append((10**12, failed_note))

    notes = []
    notes_length = 0

    for _start_line, note in sorted(map_results, key=lambda item: item[0]):
        if not note:
            continue

        notes.append(note)
        notes_length += len(note)

        if notes_length > LARGE_FILE_SUMMARY_LIMIT:
            break

    if not notes:
        result = (
            f"已读取 {path} 的 {len(chunks_to_read)} 个分块，"
            "没有找到与问题直接相关的内容。"
        )
        debug_print("TOOL summarize_large_file OUTPUT", result)
        return result

    llm = _create_summary_llm()
    debug_print(
        "TOOL summarize_large_file REDUCE INPUT",
        "\n".join([
            f"path={path!r}",
            f"notes_count={len(notes)}",
            f"notes_chars={sum(len(note) for note in notes)}",
        ]),
    )
    final_response = llm.invoke([
        SystemMessage(
            content=(
                "You are the reduce step in a map-reduce file summarizer. "
                "Answer the user's question using only the extracted notes. "
                "Be concise, preserve important line ranges, and mention uncertainty."
            )
        ),
        HumanMessage(
            content=(
                f"File: {path}\n"
                f"Total lines: {len(lines)}\n"
                f"Chunks read: {len(chunks_to_read)} of {total_chunks}\n"
                f"Question: {question}\n\n"
                "Extracted notes:\n"
                + "\n\n".join(notes)
            )
        ),
    ])

    output = final_response.content
    if total_chunks > len(chunks_to_read):
        output += (
            f"\n\n注意：文件共有 {total_chunks} 个分块，"
            f"本次最多处理前 {len(chunks_to_read)} 个分块。"
        )

    if len(output) > LARGE_FILE_SUMMARY_LIMIT:
        output = output[:LARGE_FILE_SUMMARY_LIMIT] + "\n... 大文件总结结果已截断 ..."

    debug_print("TOOL summarize_large_file OUTPUT", output)
    return output


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

    command = command.strip()
    if not command:
        result = "命令不能为空。"
        debug_print("TOOL run_command_in_container OUTPUT", result)
        return result

    try:
        shlex.split(command)
    except ValueError as exc:
        result = f"命令解析失败：{exc}"
        debug_print("TOOL run_command_in_container OUTPUT", result)
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
        return result
    except subprocess.TimeoutExpired:
        result = "检查 Docker 状态超时。请确认 Docker Desktop 已启动。"
        debug_print("TOOL run_command_in_container OUTPUT", result)
        return result

    if docker_check.returncode != 0:
        detail = (docker_check.stderr or docker_check.stdout).strip()
        result = f"Docker 不可用。请确认 Docker Desktop 已启动。\n{detail}"
        debug_print("TOOL run_command_in_container OUTPUT", result)
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
            return output

    output = _format_command_result(command, result)
    debug_print("TOOL run_command_in_container OUTPUT", output)
    return output


# 子 Agent 基础工具：不包含 delegate_to_subagent，避免递归委派。
base_tools = [
    get_weather,
    read_workspace_file,
    list_skills,
    read_skill,
    summarize_large_file,
    run_command_in_container,
]

# 父 Agent 基础工具：只提供轻量文件读取，复杂文件任务走子 Agent。
parent_base_tools = [
    get_weather,
    read_workspace_file_lite,
    list_skills,
    read_skill,
    run_command_in_container,
]
tools = base_tools
