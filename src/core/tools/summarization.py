
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from src.core.common.debug import debug_print
from src.core.config.settings import (
    LARGE_FILE_CHUNK_LINES,
    LARGE_FILE_MAP_WORKERS,
    LARGE_FILE_MAX_CHUNKS,
    LARGE_FILE_SUMMARY_LIMIT,
    MODEL,
)
from src.core.tools.workspace import _resolve_workspace_file


load_dotenv()


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
