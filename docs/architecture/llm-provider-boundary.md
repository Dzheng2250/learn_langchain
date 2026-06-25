# LLM Provider 边界与 Anthropic Message 格式

> 文档状态：Current
> 权威范围：说明 Core 如何通过 LangChain `ChatAnthropic` 接入 Anthropic Messages API，以及业务层如何处理 Anthropic message content block。
> 维护触发：修改 `src/core/llm/provider.py`、`src/core/common/content.py`、`src/core/streaming/message_events.py`、模型错误解析、trace usage 提取或内部 LLM 抽象时必须同步更新。

## 本文负责

- 说明当前默认 `AnthropicProvider` 如何创建 LangChain `ChatAnthropic`。
- 说明 Core 当前仍以 LangChain `BaseMessage`、`AIMessage`、`AIMessageChunk`、`ToolMessage` 和 `tool_calls` 作为运行时消息模型。
- 说明 Anthropic `thinking`、`text`、`tool_use`、`tool_call` 和流式 delta block 如何进入展示、工具事件、预算统计和持久化。
- 说明后续如果要实现真正 provider-neutral LLM 端口，应在哪一层拆分。

## 本文不负责

- 不定义 JSON-RPC、CLI 或 TUI 协议。
- 不定义会话持久化、Execution 恢复、长期记忆或 checkpoint 的权威状态。
- 不保存或展示完整 Prompt、thinking/reasoning 原文、模型响应或工具输出。
- 不定义具体服务商的价格、限额或模型能力。

## 当前 Provider 实现

当前默认模型 provider 是 `AnthropicProvider`，代码入口是 `src/core/llm/provider.py`：

```text
AgentGraph / Summary / Memory / FileSummary
  -> ModelProvider.create_chat_model(purpose, streaming, temperature, tools)
  -> ChatAnthropic / LangChain Runnable
  -> AIMessage / AIMessageChunk / ToolMessage / usage_metadata
```

`AnthropicProvider` 当前负责：

- 从 `LEARN_AGENT_LLM_API_KEY`、`LEARN_AGENT_MODEL` 和 `LEARN_AGENT_LLM_BASE_URL` 读取配置。
- 构造 `ChatAnthropic(model=..., api_key=..., base_url=..., streaming=..., metadata={"purpose": ...})`。
- 将 `max_retries` 设为 `0`，避免 SDK 隐式重试与项目自己的重试策略叠加。
- 当调用方传入工具 schema 时使用 `model.bind_tools(tools)`。

当前代码中不再保留 `OpenAICompatibleProvider`。如果未来要重新接入 OpenAI、MiniMax 专用 API、本地模型或原生 Anthropic HTTP adapter，应新增 adapter，而不是让业务模块直接导入具体 SDK。

## 当前耦合边界

项目已经把“创建哪个服务商客户端”集中在 `ModelProvider`，但尚未完全 provider-neutral。业务层仍然依赖 LangChain 消息对象：

```text
业务服务
  -> ModelProvider
  -> LangChain Runnable
  -> LangChain BaseMessage / AIMessage.tool_calls / ToolMessage
```

这意味着当前解耦层级是“服务商构造解耦”，不是“LLM 响应模型完全解耦”。这是一种过渡状态：足够支持 Anthropic 迁移，但未来若要支持多个协议并存，需要继续抽象内部 `LlmClient` / `LlmResult`。

## Message Content 归一化

Anthropic message content 可能是字符串，也可能是 block 列表。例如工具调用前的 AIMessage：

```json
[
  {"type": "thinking", "thinking": "..."},
  {"type": "tool_use", "id": "toolu_...", "name": "get_weather", "input": {"city": "北京"}}
]
```

最终回答通常包含 text block：

```json
[
  {"type": "thinking", "thinking": "..."},
  {"type": "text", "text": "北京当前天气晴朗..."}
]
```

业务层不得直接 `str(message.content)` 或 `repr(message.content)`。用户可见文本、摘要、记忆提取、SQLite `content` 投影、流式 token 输出和事件 fallback 都必须通过：

```python
src.core.common.content.message_content_text()
```

当前规则：

- `str` content 直接返回。
- `bytes` content 按 UTF-8 容错解码。
- `text` block 提取 `text` 字段，多个 text block 按顺序拼接。
- `thinking`、`redacted_thinking`、`reasoning` 不作为普通展示文本。
- `tool_use`、`tool_result`、`server_tool_use` 不作为普通展示文本。
- `input_json_delta`、`thinking_delta`、`signature_delta` 是 Anthropic 流式协议数据，不作为普通展示文本。
- 未知 block 才 fallback 为 JSON 文本，用于排查新协议块。

这个规则解决了一个具体问题：Anthropic 在流式工具调用时会输出 `input_json_delta`，它只是工具参数 JSON 的增量片段，例如 `{"city": "昆明"}`，不能显示给用户。

## 工具调用边界

工具调用的主路径仍依赖 LangChain 对 Anthropic `tool_use` 的归一化：

```text
Anthropic tool_use block
  -> AIMessage.tool_calls
  -> LangGraph ToolNode / ObservedToolNode
  -> ToolMessage(tool_call_id=...)
```

但为了兼容第三方 Anthropic 兼容服务或 LangChain 归一化缺失的情况，事件层有一个只读 fallback：

```python
src.core.streaming.message_events.tool_calls_from_message()
```

它的顺序是：

1. 优先读取 `AIMessage.tool_calls`。
2. 如果没有 `tool_calls`，再从 `message.content` 中识别 `tool_use` block。
3. 兼容部分服务可能返回的 `tool_call` block alias。
4. `tool_use.input` 和 `tool_call.args` 都归一化为事件里的 `args`。

这个 fallback 只用于进度事件、工具调用展示和工具预算统计。真正执行工具仍由 LangGraph ToolNode 基于 LangChain message 机制完成。

## Streaming 行为

流式输出只应该展示用户可见文本：

```text
AIMessageChunk -> message_content_text(chunk) -> token event
```

因此：

- text delta 会进入 CLI/TUI。
- thinking delta 不进入 CLI/TUI。
- input JSON delta 不进入 CLI/TUI。
- 工具调用开始由完成后的 message snapshot 生成 `tool_call_start` step event。

这能避免用户看到类似下面的协议碎片：

```json
{"type": "input_json_delta", "partial_json": "{"city": "昆明"}"}
```

## 持久化边界

SQLite 会同时保存两类数据：

```text
messages.raw      = LangChain messages_to_dict() 的完整原始消息
messages.content  = message_content_text(message) 的可见文本投影
```

规则：

- `raw` 是恢复消息、审计和后续兼容的事实来源。
- `content` 只是用于展示、搜索、摘要和调试的文本投影。
- thinking、tool_use 和 delta block 不进入 `content`，但仍可随 LangChain raw message 保留。

## Usage 与错误处理

Trace usage 提取位于 `src/core/tracing/llm.py`：

1. 优先读取 `AIMessage.usage_metadata`。
2. 如果没有 usage metadata，则读取 `response_metadata["usage"]`。
3. 如果 provider 没有返回 `total_tokens`，但返回了 input/output tokens，则本地相加。
4. stop reason 优先读取 `response_metadata["stop_reason"]`，再 fallback 到 `finish_reason`。

错误解析位于 `src/core/errors/parsers.py`：

- 默认 registry 使用 `AnthropicErrorParser`。
- Anthropic error type 会映射为 provider-neutral category，例如 rate limit、overloaded、authentication、invalid request 和 model not found。
- `AliyunErrorParser` 只作为可选 legacy adapter，用于识别 DashScope/Aliyun 的内容审查错误码。
- 上层恢复策略不应直接依赖某个服务商字符串，而应依赖 provider-neutral error category。

## 后续解耦方向

如果后续要支持原生 Anthropic HTTP adapter、OpenAI SDK Responses、MiniMax 专用 API 或本地模型，应新增内部 LLM 端口，而不是让业务代码继续直接依赖 LangChain 对象：

```python
class LlmClient(Protocol):
    def invoke(messages, tools, purpose, stream) -> LlmResult: ...
```

`LlmResult` 应统一承载：

- 用户可见文本。
- 工具调用请求。
- token usage。
- provider request id。
- stop reason。
- 可选 reasoning 摘要或隐藏推理元数据。
- 原始 provider metadata 的安全摘要。

到这一步后，Agent、Summary 和 Memory 才能只依赖项目内部模型，不关心底层是 LangChain、Anthropic SDK、OpenAI SDK 还是其他 adapter。
## Reasoning event boundary

`message_content_text()` continues to filter `thinking`, `reasoning`, `redacted_thinking`, `thinking_delta`, `input_json_delta`, and `signature_delta` from ordinary answer text. This keeps CLI/TUI token output clean and prevents tool argument deltas from appearing as prose.

When reasoning display is enabled, `stream_graph_events()` extracts reasoning with `reasoning_content_text()` and emits `reasoning_started` / `reasoning_delta` / `reasoning_finished`. This is a diagnostic/UI channel only:

- text blocks become `token` events;
- thinking/reasoning blocks become reasoning events;
- tool_use/tool_call blocks become tool step events;
- signature and redacted thinking payloads are not exposed as raw text.

The raw LangChain message may still be preserved in `messages.raw`; the projected `messages.content` remains user-visible text only.
