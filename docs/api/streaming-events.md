# Agent 流式事件参考

> 文档状态：Current
> 权威范围：`agent.chat` 和 `session.resume` 的服务端流式通知契约
> 维护触发：新增、删除或修改流式事件

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
| `request_id` | 对应原始 JSON-RPC 请求 ID |
| `run_id` | Core 为本次 chat/resume 创建的运行 ID |
| `event` | 事件类型 |
| `data` | 该事件的具体数据 |

客户端必须使用 `request_id` 过滤通知；不能假设一条连接之外不存在其他请求。

## 事件顺序

```text
0..N 个 token / step
  -> done 或 error
  -> 最终 JSON-RPC success response
```

当前实现中 `done` 和 `error` 是终止事件。最终 JSON-RPC 响应用于确认 RPC handler 已结束，并携带聚合后的最终结果。

## `token`

增量模型文本：

```json
{"event":"token","data":{"content":"部分文本"}}
```

并非所有 Provider 都保证产生 token。前端应支持只收到完整 `step.agent_message` 的情况。

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

`args` 当前可能包含工具参数。前端不得长期保存或公开展示未经处理的参数，因为其中可能含路径或用户内容。

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

当前 CLI 仅在没有收到 token 时将其作为输出兜底，避免重复显示回答。

## `done`

表示 Agent 已完成、暂停或完成诊断：

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

常见 `stop_reason`：

```text
completed
llm_not_configured
graph_step_limit
tool_call_limit
budget_limit
grant_wall_time_limit
client_disconnected
graph_error
turn_error
```

`status: paused` 表示本次请求结束，但 Execution 仍可通过 `session.status` 和 `session.resume` 继续。

## `error`

```json
{
  "event": "error",
  "data": {
    "type": "provider_error",
    "stop_reason": "graph_error",
    "message": "安全的用户可见错误",
    "error_category": "rate_limited",
    "error_action": "pause",
    "retryable": true
  }
}
```

Provider 错误还可能包含 `provider`、`provider_code` 和 `http_status`。客户端应展示安全的 `message`，不要把未知内部字段直接暴露给用户。

## 断线语义

客户端断开后：

- 已开始的当前 Slice 可以继续结束。
- Core 不再向断开的客户端发送事件。
- Core 会在开始下一 Slice 前暂停 Execution。
- 客户端应使用 `session.status` 查询状态，而不是自动重发 `agent.chat`。

## 兼容性要求

当前 `event.data` 仍是通用字典。前端应忽略未知事件字段，并对未知 `step.type` 使用通用进度展示。未来会逐步将各事件改为严格模型。
