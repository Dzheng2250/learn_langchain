# Agent 流式事件参考

> 文档状态：Current
> 权威范围：`agent.chat` 与 `session.resume` 的服务端流式通知契约
> 维护触发：新增、删除或修改流式事件

## 本文负责

- 定义 `agent.event` 的外层字段、事件顺序和 payload。
- 说明 CLI、TUI 或第三方前端如何处理 token、工具步骤、模型重试、错误和 done。

## 本文不负责

- 不解释 Agent 为什么产生某个事件；见 [Agent 执行架构](/docs/architecture/agent-execution-architecture.md)。
- 不定义 Telemetry Event 或 System Trace；见 [Event 系统](/docs/architecture/event-system.md) 和 [System Trace](/docs/architecture/system-tracing.md)。

`agent.chat` 和 `session.resume` 执行期间，Core 使用 JSON-RPC notification 推送事件：

```json
{
  "jsonrpc": "2.0",
  "method": "agent.event",
  "params": {
    "request_id": "chat-1",
    "run_id": "core-generated-run-id",
    "event": "token",
    "data": {"content": "正在"}
  }
}
```

## 外层字段

| 字段 | 含义 |
|---|---|
| `request_id` | 对应原始 JSON-RPC 请求 ID。 |
| `run_id` | Core 为本次 chat/resume 创建的运行 ID。 |
| `event` | 事件类型。 |
| `data` | 事件 payload。前端必须忽略未知字段。 |

客户端必须用 `request_id` 过滤通知，不能假设单连接内只有一个请求。

## 事件顺序

```text
0..N 个 token / step / model_retry_*
  -> done 或 error
  -> 最终 JSON-RPC success response
```

`done` 和 `error` 是终止事件。最终 JSON-RPC 响应用于确认 handler 已结束，并携带聚合后的最终结果。

## `token`

模型增量文本：

```json
{"event":"token","data":{"content":"部分文本","attempt_id":"..."}}
```

| 字段 | 含义 |
|---|---|
| `content` | 增量文本。 |
| `attempt_id` | 可选。当前 LLM 尝试 ID；模型重试时会变化。 |

并非所有 Provider 都保证产生 token。前端必须支持只收到完整 `step.agent_message` 的情况。

## 模型重试事件

Core 会在当前 LLM 调用内部处理短期限流、网络中断和临时服务不可用。重试事件只描述当前模型调用，不代表整个 Agent Turn 已经失败。

### `model_retry_scheduled`

```json
{
  "event": "model_retry_scheduled",
  "data": {
    "purpose": "parent_agent",
    "attempt": 1,
    "next_attempt": 2,
    "max_attempts": 3,
    "delay_seconds": 1.5,
    "error_category": "service_unavailable",
    "request_id": "provider-request-id"
  }
}
```

前端应显示“模型临时失败，稍后重试”，不要提示用户手动 resume。

### `model_attempt_invalidated`

如果一次模型尝试已经输出了部分 token，随后该尝试失败并进入重试，Core 会发送：

```json
{"event":"model_attempt_invalidated","data":{"attempt":1,"error_category":"timeout"}}
```

前端处理规则：

- 不删除已经展示的草稿。
- 将草稿标记为 stale/incomplete，说明它不是最终回答。
- 后续成功尝试的 token 才能作为当前回复继续展示。
- stale 草稿不得写入正式对话历史。

### `model_retry_exhausted`

```json
{"event":"model_retry_exhausted","data":{"error_category":"rate_limited","attempt":3}}
```

表示当前 LLM 调用的模型级重试预算耗尽。随后 Core 会根据错误类别发送 `error` 或 `done(status=paused|terminated)`。

## `tool_approval_required`

工具策略要求人工确认时发送：

```json
{
  "event": "tool_approval_required",
  "data": {
    "request_id": "approval-id",
    "tool": "run_command_in_container",
    "args": {"command": "python -m unittest"},
    "capabilities": ["command_execution", "file_read"],
    "persistable": true
  }
}
```

随后 Execution 以 `stop_reason=tool_approval` 暂停。参数已经过敏感字段过滤和长度限制。

## `step`

## `tool_approval_required`

工具策略要求人工确认时发送：

```json
{
  "event": "tool_approval_required",
  "data": {
    "request_id": "approval-id",
    "tool": "run_command_in_container",
    "args": {"command": "python -m unittest"},
    "capabilities": ["command_execution", "file_read"],
    "persistable": true
  }
}
```

随后 Execution 以 `stop_reason=tool_approval` 暂停。参数已经过敏感字段过滤和长度限制，前端不得把它当作完整原始调用存档。

## `step`

步骤事件用于展示 Agent 进度。

### Agent 开始

```json
{"event":"step","data":{"type":"agent_start","message":"Agent turn started."}}
```

### 工具调用开始

```json
{
  "event": "step",
  "data": {
    "type": "tool_call_start",
    "tool": "read_workspace_file_lite",
    "args": {"path":"README.md"},
    "id": "tool-call-id"
  }
}
```

`args` 是经过前端可见策略处理的参数预览，可能被截断或脱敏。前端不应长期保存未处理参数，因为其中可能包含路径或用户内容。

### 工具调用结果

```json
{
  "event": "step",
  "data": {
    "type": "tool_call_result",
    "tool": "read_workspace_file_lite",
    "tool_call_id": "tool-call-id",
    "content": "结果预览"
  }
}
```

`content` 是截断后的预览，不是稳定的完整工具结果接口。

### 完整 Agent 消息

```json
{"event":"step","data":{"type":"agent_message","content":"完整回答"}}
```

CLI/TUI 仅在没有收到 token 时将其作为输出兜底，避免重复显示回答。

## `done`

表示 Agent 已完成、暂停或终止：

```json
{
  "event": "done",
  "data": {
    "run_id": "...",
    "status": "ok",
    "execution_id": "...",
    "stop_reason": "completed",
    "durability": "committed",
    "maintenance_status": "pending"
  }
}
```

常见 `status`：

| status | 含义 |
|---|---|
| `ok` | 本次请求完成。 |
| `paused` | Execution 仍可恢复，需要 `session.resume`。 |
| `terminated` | 本轮被 Core 主动终止。若 `auto_recovered=true`，Session 已回到上一轮成功提交状态。 |

自动恢复的典型 payload：

```json
{
  "event": "done",
  "data": {
    "status": "terminated",
    "auto_recovered": true,
    "failed_turn_saved": false,
    "failure_source": "agent_turn",
    "failure_scope": "current_turn",
    "failure_stage": "parent_model_provider",
    "user_action": "revise_input_and_retry"
  }
}
```

后台摘要、长期记忆等维护任务失败不会通过当前请求的 `done` 表示；应通过 `session.status.maintenance` 和 Trace/Telemetry 排查。

## `error`

```json
{
  "event": "error",
  "data": {
    "type": "provider_error",
    "message": "安全的用户可见错误",
    "error_category": "rate_limited",
    "error_action": "pause",
    "retryable": true,
    "failure_source": "agent_turn",
    "failure_scope": "current_turn",
    "failure_stage": "parent_model_provider",
    "user_action": "resume_later"
  }
}
```

前端应展示 `message`，并可在调试模式展示 `failure_*` 与 `request_id`。不要展示 API key、完整 Provider 原始响应或 traceback。

## 断线语义

客户端断开后：

- 已开始的当前 Slice 可以继续结束。
- Core 不再向断开的客户端发送事件。
- Core 会在开始下一 Slice 前暂停 Execution。
- 客户端应使用 `session.status` 查询状态，而不是自动重发非幂等请求。
## Reasoning / thinking events

Core keeps normal assistant text and provider reasoning on separate event lines. `token` is still only user-visible answer text. Anthropic `thinking`, `reasoning`, and `thinking_delta` blocks are exposed, when configured, through these events:

```json
{"event":"reasoning_started","data":{"source":"parent_agent","display":"metadata","expanded":false}}
{"event":"reasoning_delta","data":{"content":"...","char_count":120,"redacted":false}}
{"event":"reasoning_finished","data":{"char_count":856,"redacted":false}}
```

Frontend rules:

- `reasoning_delta.content` is present only when `LEARN_AGENT_REASONING_DISPLAY` is `collapsed` or `expanded`.
- `metadata` mode sends start/finish and character counts, but not raw reasoning text.
- `redacted_thinking` never exposes raw content; clients should show only redacted metadata.
- Reasoning events must not be appended to the final assistant message or saved as user-visible answer text.
