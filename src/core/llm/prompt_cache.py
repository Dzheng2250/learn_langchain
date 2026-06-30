"""LangChain-side Anthropic prompt cache injection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable


@dataclass(frozen=True)
class PromptCacheSettings:
    """Prompt cache switches for Anthropic-compatible providers."""

    enabled: bool = True
    ttl: str = "5m"
    cache_tools: bool = True
    cache_system: bool = True
    cache_messages: bool = True

    @property
    def cache_control(self) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        control: dict[str, Any] = {"type": "ephemeral"}
        if self.ttl:
            control["ttl"] = self.ttl
        return control


class PromptCachePolicy:
    """Attach cache_control to stable LangChain tool, system, and message boundaries."""

    def __init__(self, settings: PromptCacheSettings | None = None) -> None:
        self.settings = settings or PromptCacheSettings()

    def apply_tools(self, tools: list | None) -> list | None:
        if not tools:
            return tools
        formatted = [_without_cache_control(dict(tool)) for tool in tools]
        cache_control = self.settings.cache_control
        if cache_control and self.settings.cache_tools:
            formatted[-1] = {**formatted[-1], "cache_control": dict(cache_control)}
        return formatted

    def apply_messages(self, messages):
        cache_control = self.settings.cache_control
        if not cache_control or not isinstance(messages, list):
            return messages

        updated = [
            _message_without_cache(_message_with_text_blocks(message))
            for message in messages
        ]
        code_execution_tool_ids = _collect_code_execution_tool_ids(updated)
        if self.settings.cache_system:
            for index, message in enumerate(updated):
                if isinstance(message, SystemMessage):
                    updated[index] = _message_with_cache(
                        message, cache_control, code_execution_tool_ids
                    )
                    break

        if self.settings.cache_messages:
            index = _last_stable_history_index(updated, code_execution_tool_ids)
            if index is not None:
                updated[index] = _message_with_cache(
                    updated[index], cache_control, code_execution_tool_ids
                )
        return updated


class PromptCacheRunnable(Runnable):
    """Rewrite LangChain messages before every supported Runnable entry point."""

    def __init__(self, inner, policy: PromptCachePolicy | None = None) -> None:
        self.inner = inner
        self.policy = policy or PromptCachePolicy()

    def invoke(self, input, config=None, **kwargs):
        return self.inner.invoke(self.policy.apply_messages(input), config=config, **kwargs)

    async def ainvoke(self, input, config=None, **kwargs):
        return await self.inner.ainvoke(
            self.policy.apply_messages(input),
            config=config,
            **kwargs,
        )

    def stream(self, input, config=None, **kwargs):
        yield from self.inner.stream(
            self.policy.apply_messages(input),
            config=config,
            **kwargs,
        )

    async def astream(self, input, config=None, **kwargs):
        async for chunk in self.inner.astream(
            self.policy.apply_messages(input),
            config=config,
            **kwargs,
        ):
            yield chunk

    def batch(self, inputs, config=None, **kwargs):
        return self.inner.batch(
            [self.policy.apply_messages(item) for item in inputs],
            config=config,
            **kwargs,
        )

    async def abatch(self, inputs, config=None, **kwargs):
        return await self.inner.abatch(
            [self.policy.apply_messages(item) for item in inputs],
            config=config,
            **kwargs,
        )

    def __getattr__(self, name: str):
        return getattr(self.inner, name)


def _last_stable_history_index(
    messages: list,
    code_execution_tool_ids: set[str],
) -> int | None:
    """Find the deepest cacheable message without caching a new user request."""
    search_end = (
        len(messages) - 1
        if messages and isinstance(messages[-1], HumanMessage)
        else len(messages)
    )
    for index in range(search_end - 1, -1, -1):
        message = messages[index]
        if (
            not isinstance(message, SystemMessage)
            and _has_cacheable_content(message, code_execution_tool_ids)
        ):
            return index
    return None


def _message_with_text_blocks(message: BaseMessage) -> BaseMessage:
    content = _content_as_blocks(message.content)
    if content is message.content:
        return message
    return message.model_copy(update={"content": content})


def _message_with_cache(
    message: BaseMessage,
    cache_control: dict[str, Any],
    code_execution_tool_ids: set[str],
) -> BaseMessage:
    content = _content_with_cache(
        message.content,
        cache_control,
        code_execution_tool_ids,
    )
    if content is message.content:
        return message
    return message.model_copy(update={"content": content})


def _message_without_cache(message: BaseMessage) -> BaseMessage:
    content = _without_cache_control(message.content)
    if content == message.content:
        return message
    return message.model_copy(update={"content": content})


def _without_cache_control(value: Any) -> Any:
    """Copy nested provider payloads while removing pre-existing breakpoints."""
    if isinstance(value, dict):
        return {
            key: _without_cache_control(item)
            for key, item in value.items()
            if key != "cache_control"
        }
    if isinstance(value, list):
        return [_without_cache_control(item) for item in value]
    return value


def _content_as_blocks(content: Any) -> Any:
    if isinstance(content, str):
        if not content:
            return content
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return content

    changed = False
    blocks = []
    for block in content:
        if isinstance(block, dict) and "type" not in block and isinstance(block.get("text"), str):
            blocks.append({"type": "text", **block})
            changed = True
        else:
            blocks.append(block)
    return blocks if changed else content


def _content_with_cache(
    content: Any,
    cache_control: dict[str, Any],
    code_execution_tool_ids: set[str],
) -> Any:
    content = _content_as_blocks(content)
    if not isinstance(content, list):
        return content

    blocks = list(content)
    for index in range(len(blocks) - 1, -1, -1):
        block = blocks[index]
        if (
            isinstance(block, dict)
            and block.get("type", "text") == "text"
            and isinstance(block.get("text"), str)
            and block.get("text")
        ):
            blocks[index] = {
                "type": "text",
                **block,
                "cache_control": dict(cache_control),
            }
            return blocks

    for index in range(len(blocks) - 1, -1, -1):
        block = blocks[index]
        if _is_cacheable_block(block, code_execution_tool_ids):
            blocks[index] = {**block, "cache_control": dict(cache_control)}
            return blocks
    return content


def _has_cacheable_content(
    message: BaseMessage,
    code_execution_tool_ids: set[str],
) -> bool:
    content = message.content
    if isinstance(content, str):
        return bool(content)
    if isinstance(content, list):
        return any(
            _is_cacheable_block(block, code_execution_tool_ids)
            for block in content
        )
    return False


def _collect_code_execution_tool_ids(messages: list) -> set[str]:
    tool_ids: set[str] = set()
    for message in messages:
        content = getattr(message, "content", None)
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            caller = block.get("caller")
            if (
                isinstance(caller, dict)
                and str(caller.get("type", "")).startswith("code_execution")
                and block.get("id")
            ):
                tool_ids.add(str(block["id"]))
    return tool_ids


def _is_cacheable_block(block: Any, code_execution_tool_ids: set[str]) -> bool:
    if not isinstance(block, dict):
        return False
    block_type = block.get("type", "text")
    if block_type in {"input_json_delta", "thinking_delta", "signature_delta"}:
        return False
    if block_type in {
        "code_execution_tool_result",
        "bash_code_execution_tool_result",
        "text_editor_code_execution_tool_result",
    }:
        return False
    if block_type == "tool_use":
        caller = block.get("caller")
        if isinstance(caller, dict) and str(caller.get("type", "")).startswith(
            "code_execution"
        ):
            return False
    if (
        block_type == "tool_result"
        and str(block.get("tool_use_id", "")) in code_execution_tool_ids
    ):
        return False
    if block_type == "text":
        return isinstance(block.get("text"), str) and bool(block.get("text"))
    return bool(block_type)

__all__ = ["PromptCachePolicy", "PromptCacheRunnable", "PromptCacheSettings"]
