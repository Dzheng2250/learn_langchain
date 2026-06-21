"""Workspace-bound map-reduce file summarization tool."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from src.config.settings import (
    LARGE_FILE_CHUNK_LINES,
    LARGE_FILE_MAP_WORKERS,
    LARGE_FILE_MAX_CHUNKS,
    LARGE_FILE_SUMMARY_LIMIT,
)
from src.core.llm.contracts import LlmPurpose, ModelProvider
from src.core.tools.workspace import read_workspace_lines


def create_summarize_large_file(root: Path, model_provider: ModelProvider):
    """Create a Workspace-bound parallel map-reduce file summarization tool."""
    provider = model_provider

    def llm():
        """Create an isolated deterministic model call for one map/reduce step."""
        return provider.create_chat_model(
            LlmPurpose.FILE_SUMMARY,
            temperature=0,
            streaming=False,
        )

    @tool
    def summarize_large_file(path: str, question: str) -> str:
        """Summarize or search a large file in the current workspace."""
        try:
            _target, lines = read_workspace_lines(root, path)
        except (OSError, ValueError) as exc:
            return f"Large-file summary rejected: {exc}"

        chunks = [
            (start + 1, min(start + LARGE_FILE_CHUNK_LINES, len(lines)), "\n".join(
                f"{number}: {line}"
                for number, line in enumerate(
                    lines[start:start + LARGE_FILE_CHUNK_LINES],
                    start=start + 1,
                )
            ))
            for start in range(0, len(lines), LARGE_FILE_CHUNK_LINES)
        ][:LARGE_FILE_MAX_CHUNKS]
        if not chunks:
            return f"{path} is empty."

        def summarize(chunk) -> str:
            """Extract question-relevant notes from one numbered file chunk."""
            start, end, content = chunk
            response = llm().invoke(
                [
                    SystemMessage(content="Extract only facts relevant to the question. Keep line references."),
                    HumanMessage(content=f"Question: {question}\nFile: {path}\nLines {start}-{end}:\n{content}"),
                ]
            )
            return f"Lines {start}-{end}:\n{response.content}"

        with ThreadPoolExecutor(max_workers=min(LARGE_FILE_MAP_WORKERS, len(chunks))) as executor:
            notes = list(executor.map(summarize, chunks))
        notes_text = "\n\n".join(notes)[:LARGE_FILE_SUMMARY_LIMIT]
        response = llm().invoke(
            [
                SystemMessage(content="Answer using only the extracted file notes. Preserve useful line ranges."),
                HumanMessage(content=f"Question: {question}\nFile: {path}\n\n{notes_text}"),
            ]
        )
        return str(response.content)[:LARGE_FILE_SUMMARY_LIMIT]

    return summarize_large_file
