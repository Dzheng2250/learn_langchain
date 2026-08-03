"""Completion-state helpers shared by model and streaming boundaries."""


class ModelOutputLimitError(RuntimeError):
    """Raised when a provider exhausts its output budget before completion."""


def response_stop_reason(message) -> str:
    """Read a provider stop reason from a LangChain message defensively."""
    metadata = getattr(message, "response_metadata", None)
    if not isinstance(metadata, dict):
        return ""
    value = metadata.get("stop_reason") or metadata.get("finish_reason")
    return str(value or "").strip().lower()


def ensure_complete_response(message) -> None:
    """Reject output-budget truncation before it enters graph state/history."""
    if response_stop_reason(message) == "max_tokens":
        raise ModelOutputLimitError(
            "The model exhausted its output token budget before producing a "
            "complete response. Increase LEARN_AGENT_LLM_MAX_TOKENS and resume."
        )
