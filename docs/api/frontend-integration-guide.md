# 前端开发完整接入指南

> 文档状态：Current
> 权威范围：CLI、TUI、桌面 GUI 和第三方本地前端接入 Core daemon 的端到端实现流程
> 维护触发：IPC、公开 RPC、流式事件、审批、恢复或客户端职责发生变化

## 本文负责

- 说明前端如何发现 Core、鉴权、发送请求、消费流式事件并处理最终结果。
- 给出聊天、工具审批、暂停恢复、断线恢复和错误展示的完整客户端流程。
- 明确前端状态、身份字段、安全要求、兼容规则和当前能力边界。

## 本文不负责

- 不重复定义每个 RPC 和事件的全部稳定字段；字段权威来源分别是 [RPC 方法参考](/docs/api/rpc-reference.md)和[流式事件参考](/docs/api/streaming-events.md)。
- 不解释 Agent、数据库、工具或 checkpoint 的内部实现。
- 不提供浏览器可直接访问的 HTTP/WebSocket API；当前公开传输是本机 TCP。

本文是开发新前端时的首要入口。当前系统已经分离 Core 与前端进程，但公开契约仍由多份专项文档共同组成。本指南负责把这些契约组织成一个可实现的客户端流程。

> 当前契约成熟度：请求参数已经使用严格 Pydantic 模型校验；RPC 成功结果和 `agent.event.data` 多数仍是内部代码构造的 `dict`，尚未形成可自动生成 TypeScript 类型的严格响应模型。本文只记录当前代码实际发送的字段，并明确标记可选字段和缺口。

## 1. 当前可接入的前端类型

| 前端类型 | 是否可直接接入 | 说明 |
|---|---:|---|
| Python CLI/TUI | 是 | 可使用 TCP、读取本地 token，并复用 `src.ipc` wire models。 |
| 桌面 GUI | 是 | 后端进程可连接本机 TCP；渲染进程不应直接读取数据库。 |
| Node.js 本地客户端 | 是 | 需要实现 NDJSON 分帧、JSON-RPC 和 token 读取。 |
| 浏览器网页 | 否 | 浏览器不能直接连接原始 TCP，也不应直接读取 daemon token。 |

Web 前端必须增加受信任的本地 HTTP/WebSocket bridge。Bridge 负责持有 daemon token、转发 JSON-RPC、限制 Origin 并执行用户身份校验。不要把 Core TCP 端口或 token 暴露给网页脚本。

## 2. 前端唯一允许依赖的边界

```text
Frontend
  -> TCP + UTF-8 NDJSON
  -> JSON-RPC 2.0 request
  <- 0..N agent.event notifications
  <- one final JSON-RPC response
Core daemon
```

前端可以依赖 [IPC 协议](/docs/api/ipc-protocol.md)、[RPC 方法参考](/docs/api/rpc-reference.md)、[流式事件参考](/docs/api/streaming-events.md)和[错误与恢复参考](/docs/api/error-reference.md)。

前端不得：

- 导入 `src.core.*`、调用 Agent service 或工具实现。
- 读取或修改 `state.db`、checkpoint、Trace 或 Telemetry 数据库。
- 自行创建 `run_id`、`execution_id`、approval ID 或 Trace ID。
- 把工具预览、reasoning 或 stale 草稿写成正式消息历史。

## 3. 连接、鉴权与分帧

默认 Core 监听 loopback TCP 地址。每个请求使用一条独立连接：

1. 从用户级 runtime 目录读取 daemon token。
2. 建立 TCP 连接。
3. 发送一行 UTF-8 JSON，并以 `\n` 结束。
4. 持续逐行读取 notification 和最终 response。
5. 收到对应请求 ID 的最终 response 后关闭连接。

请求 envelope：

```json
{
  "jsonrpc": "2.0",
  "id": "ui-8e0c7a",
  "method": "agent.chat",
  "params": {
    "auth_token": "<daemon-token>",
    "workspace_root": "D:\\project",
    "session_name": "default",
    "message": "分析当前项目",
    "goal_mode": false
  }
}
```

约束：

- `id` 由客户端生成，在当前连接中唯一。
- 每条 JSON 消息占一行；不能把格式化 JSON 跨多行发送。
- 文本必须使用 UTF-8，序列化中文时无需转义为 ASCII。
- token 必须放在每次请求的 `params.auth_token`，不得记录到日志。
- 当前不支持 batch，也不支持同一连接并发多个请求。
- 客户端的单行读取上限必须至少覆盖 Core 的 `CORE_MAX_MESSAGE_BYTES`（当前默认 1 MiB）。Python
  `asyncio.open_connection()` 默认约 64 KiB，必须显式传入 `limit=CORE_MAX_MESSAGE_BYTES + 1`；
  否则 `session.history` 等较大响应会在合法消息到达时误报分隔符不存在。

## 4. 身份与关联规则

| 字段 | 生成方 | 生命周期 | 前端用途 |
|---|---|---|---|
| JSON-RPC `id` | 前端 | 一次 RPC | 匹配最终响应。 |
| `request_id` | Core 复制请求 ID | 一次 RPC | 过滤该请求的通知。 |
| `run_id` | Core | 一次 chat/resume | 关联流式事件和诊断信息。 |
| `execution_id` | Core | 可跨 resume | 展示长期执行和恢复状态。 |
| `session_name` | 前端 | Workspace 内稳定 | 用户选择会话。 |
| approval `request_id` | Core | 一次工具审批 | 提交审批决定。 |

事件中的 `request_id` 与 Provider request ID 含义不同。后者只用于模型故障排查。

## 5. 完整 RPC JSON 目录

所有示例都省略真实 token。发送时必须把 `auth_token` 放入 `params`。成功响应统一使用：

```json
{"jsonrpc":"2.0","id":"client-1","result":{}}
```

失败响应统一使用：

```json
{"jsonrpc":"2.0","id":"client-1","error":{"code":-32602,"message":"Invalid params","data":[]}}
```

### 5.1 `core.ping`

请求：

```json
{
  "jsonrpc": "2.0",
  "id": "ping-1",
  "method": "core.ping",
  "params": {"auth_token": "<daemon-token>"}
}
```

响应：

```json
{
  "jsonrpc": "2.0",
  "id": "ping-1",
  "result": {"status": "ok", "server_version": "0.1.0", "uptime_ms": 12345}
}
```

### 5.2 `core.shutdown`

```json
{
  "jsonrpc": "2.0",
  "id": "shutdown-1",
  "method": "core.shutdown",
  "params": {"auth_token": "<daemon-token>"}
}
```

```json
{"jsonrpc":"2.0","id":"shutdown-1","result":{"status":"shutting_down"}}
```

### 5.3 `agent.chat`

```json
{
  "jsonrpc": "2.0",
  "id": "chat-1",
  "method": "agent.chat",
  "params": {
    "auth_token": "<daemon-token>",
    "workspace_root": "D:\\project",
    "session_name": "default",
    "message": "规划并实现阶乘函数",
    "goal_mode": true
  }
}
```

该请求先产生 0..N 条 `agent.event`，最后返回聚合结果。成功示例：

```json
{
  "jsonrpc": "2.0",
  "id": "chat-1",
  "result": {
    "status": "ok",
    "run_id": "run-id",
    "workspace_id": "workspace-uuid",
    "session_id": "session-uuid",
    "session_name": "default",
    "execution_id": "execution-id",
    "stop_reason": "completed",
    "tool_call_count": 3,
    "slices_used": 1,
    "goal_mode": true,
    "durability": "committed",
    "maintenance_status": "pending",
    "memory_status": "not_scheduled",
    "memory_request_explicit": false,
    "context_tokens": 11487
  }
}
```

`execution_id` 在未创建 Execution 的诊断路径中可能为 `null` 或缺省。`context_tokens` 是本轮最后记录的模型输入上下文量，不是本轮输入与输出 token 总和。

### 5.4 `session.status`

```json
{
  "jsonrpc": "2.0",
  "id": "status-1",
  "method": "session.status",
  "params": {
    "auth_token": "<daemon-token>",
    "workspace_root": "D:\\project",
    "session_name": "default"
  }
}
```

```json
{
  "jsonrpc": "2.0",
  "id": "status-1",
  "result": {
    "workspace_id": "workspace-uuid",
    "session_id": "session-uuid",
    "session_name": "default",
    "context_tokens": 11487,
    "pending_execution": null,
    "execution_recoverable": false,
    "checkpoint_state": null,
    "maintenance": {
      "pending": 0,
      "running": 0,
      "failed": 0,
      "recent_failures": []
    }
  }
}
```

若存在暂停执行，`pending_execution` 为对象，并至少携带 Execution 身份、状态、停止原因和 goal 模式；客户端应把它视为可扩展对象并忽略未知字段。归档 Session 返回 `status="archived"`。

### 5.5 `session.history`

前端启动、重连或切换 Session 时，先读取最近一页已提交历史，再读取 `session.status`：

```json
{
  "jsonrpc": "2.0",
  "id": "history-1",
  "method": "session.history",
  "params": {
    "auth_token": "<daemon-token>",
    "workspace_root": "D:\\project",
    "session_name": "default",
    "before_turn": null,
    "limit_turns": 30
  }
}
```

结果中的 `turns` 按正序显示。继续加载上一页时，把上一页的 `next_before_turn` 原样作为新的
`before_turn`；它是排他游标。只有 `has_more=true` 时才继续请求。前端应按 block 类型渲染：

- `text`：用户文本按字面转义，assistant 文本可走 Markdown。
- `reasoning`：默认折叠；`redacted=true` 或没有 `content` 时只显示元数据。
- `tool_call/tool_result`：默认折叠，只显示 Core 返回的安全参数和结果预览。

如果单个 Turn 超过历史响应预算，Core 会保持 Turn/message/block 边界并截断超大正文。
`truncated=true` 表示当前内容只是安全投影；`char_count` 和 `original_bytes` 表示截断前体积。
前端应显示明确的截断标记，不应尝试把下一页内容拼接到这个 block。

该接口不包含未提交草稿。不要把断线前缓存的 token 与返回历史直接拼接；应先把草稿标记为
incomplete，再以 Core 返回的提交历史为准。

### 5.6 `session.resume`

`instruction` 是附加恢复指令，可以为空；它不是新的聊天消息。

```json
{
  "jsonrpc": "2.0",
  "id": "resume-1",
  "method": "session.resume",
  "params": {
    "auth_token": "<daemon-token>",
    "workspace_root": "D:\\project",
    "session_name": "default",
    "instruction": "继续执行，但先检查现有文件"
  }
}
```

`session.resume` 与 `agent.chat` 一样先发送 `agent.event`，再返回最终结果。没有待恢复执行时：

```json
{
  "jsonrpc": "2.0",
  "id": "resume-1",
  "result": {
    "status": "idle",
    "run_id": "run-id",
    "workspace_id": "workspace-uuid",
    "session_id": "session-uuid",
    "session_name": "default",
    "message": "Session has no pending execution to resume."
  }
}
```

### 5.7 `session.discard`

```json
{
  "jsonrpc": "2.0",
  "id": "discard-1",
  "method": "session.discard",
  "params": {
    "auth_token": "<daemon-token>",
    "workspace_root": "D:\\project",
    "session_name": "default"
  }
}
```

```json
{"jsonrpc":"2.0","id":"discard-1","result":{"status":"discarded","execution_id":"execution-id"}}
```

没有待执行任务时返回 idle 语义；归档 Session 返回 archived 语义。前端必须按 `result.status` 分支，不能只判断 HTTP/TCP 成功。

### 5.8 `session.delete`

归档：

```json
{
  "jsonrpc": "2.0",
  "id": "delete-1",
  "method": "session.delete",
  "params": {
    "auth_token": "<daemon-token>",
    "workspace_root": "D:\\project",
    "session_name": "default",
    "hard_delete": false
  }
}
```

```json
{"jsonrpc":"2.0","id":"delete-1","result":{"status":"archived","mode":"archive","session_name":"default"}}
```

永久删除只需把 `hard_delete` 设为 `true`：

```json
{"jsonrpc":"2.0","id":"delete-2","result":{"status":"deleted","mode":"hard_delete","session_name":"default"}}
```

硬删除不可恢复，前端必须二次确认。

### 5.9 `session.reset`

```json
{
  "jsonrpc": "2.0",
  "id": "reset-1",
  "method": "session.reset",
  "params": {
    "auth_token": "<daemon-token>",
    "workspace_root": "D:\\project",
    "session_name": "default"
  }
}
```

```json
{
  "jsonrpc": "2.0",
  "id": "reset-1",
  "result": {
    "status": "ok",
    "workspace_id": "workspace-uuid",
    "session_id": "session-uuid",
    "session_name": "default",
    "recovered_messages": 24
  }
}
```

### 5.10 `approval.list`

```json
{
  "jsonrpc": "2.0",
  "id": "approval-list-1",
  "method": "approval.list",
  "params": {
    "auth_token": "<daemon-token>",
    "workspace_root": "D:\\project",
    "session_name": "default"
  }
}
```

```json
{
  "jsonrpc": "2.0",
  "id": "approval-list-1",
  "result": {
    "requests": [
      {
        "request_id": "approval-id",
        "workspace_id": "workspace-uuid",
        "session_id": "session-uuid",
        "execution_id": "execution-id",
        "tool_call_id": "tool-call-id",
        "tool": "write_workspace_file",
        "args": {"path": "src/a.py", "content": "<120 chars omitted>"},
        "capabilities": ["file_write"],
        "persistable": true,
        "status": "pending"
      }
    ]
  }
}
```

审批对象目前尚未建模为严格公开响应类型，实际可能增加 `reason`、时间或规则字段；前端只应依赖上述核心字段并忽略未知字段。

### 5.11 `approval.resolve`

```json
{
  "jsonrpc": "2.0",
  "id": "approval-resolve-1",
  "method": "approval.resolve",
  "params": {
    "auth_token": "<daemon-token>",
    "workspace_root": "D:\\project",
    "session_name": "default",
    "request_id": "approval-id",
    "response": "allow_once"
  }
}
```

该 RPC 会恢复原 Execution，所以返回模式与 `session.resume` 相同：先发送 0..N 条 `agent.event`，最后返回完成、再次暂停或失败的聚合结果。

允许值：

```text
allow_once | allow_session | allow_workspace
deny_once  | deny_session  | deny_workspace
```

当审批请求 `persistable=false` 时，只能发送 `allow_once` 或 `deny_once`。

### 5.12 `approval.mode.get`

使用与 `approval.list` 相同的 Session scope。返回 `default_mode`、可为空的 `override_mode`、`effective_mode`、服务端 `supported_modes` 和 `pending_count`。前端必须使用服务端返回的模式列表，不能假定以后永远只有 `manual/accept_all`。

### 5.13 `approval.mode.set`

接收 `mode=inherit|<supported mode>`。设置 `accept_all` 时必须同时传 `acknowledge_risk=true`，并在 UI 中明确说明：自动模式只处理之后产生的 `ASK`，已有 pending 不变，硬安全边界仍然生效。响应中的 `existing_pending_unchanged=true` 不是失败，而是提示客户端继续保留审批队列入口。

所有前端应把模式管理和具体请求处理视为两个接口：模式决定未来 `ASK` 如何协调；`approval.list/resolve` 处理已经存在的人工请求。切换模式后不得在客户端自行批量 resolve pending。
## 6. 最小客户端实现

```python
async def request(method, params, on_event):
    reader, writer = await asyncio.open_connection(host, port)
    request_id = new_request_id()
    body = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {"auth_token": read_token(), **params},
    }
    writer.write((json.dumps(body, ensure_ascii=False) + "\n").encode("utf-8"))
    await writer.drain()

    try:
        while True:
            line = await reader.readline()
            if not line:
                raise ConnectionInterrupted()
            message = json.loads(line.decode("utf-8"))
            if message.get("method") == "agent.event":
                event = message["params"]
                if event.get("request_id") == request_id:
                    await on_event(event)
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RpcError(message["error"])
            return message["result"]
    finally:
        writer.close()
        await writer.wait_closed()
```

生产客户端还必须限制 frame 大小、处理无效 UTF-8/JSON、验证对象形状、设置连接超时，并确保错误路径也关闭 socket。

## 7. 前端状态模型

推荐将连接状态和 Agent 状态分开：

```text
ConnectionState = disconnected | connecting | connected | interrupted
RequestState    = idle | running | awaiting_approval | paused | completed | failed
DraftState      = empty | streaming | stale | committed
```

前端至少保存：

```text
workspace_root
session_name
goal_mode
active_request_id
active_run_id
active_execution_id
request_state
answer_draft
stale_drafts[]
pending_approvals[]
last_error
```

`answer_draft` 只是显示状态。正式历史由 Core 提交，前端不能因为收到 token 就假定回答已经持久化。

## 8. 发起普通对话

调用 `agent.chat`：

```json
{
  "workspace_root": "D:\\project",
  "session_name": "default",
  "message": "修复测试失败",
  "goal_mode": false
}
```

推荐流程：

1. 用户提交输入时清空当前回答草稿并滚动到最新位置。
2. 设置 `RequestState=running`，禁用重复提交。
3. 消费所有 `agent.event`。
4. 收到 `done` 后根据 `status` 更新界面，但继续读取。
5. 收到最终 JSON-RPC response 后才结束本次连接。
6. 只有 `durability=committed` 才可显示“已保存”。

`goal_mode=true` 只允许父 Agent 使用私有任务规划工具，不是公开任务管理 API。

## 9. 流式事件处理

```json
{
  "jsonrpc": "2.0",
  "method": "agent.event",
  "params": {
    "request_id": "ui-8e0c7a",
    "run_id": "core-run-id",
    "event": "token",
    "data": {"content": "增量文本"}
  }
}
```

| 事件 | 必须行为 |
|---|---|
| `token` | 原样追加 `data.content`；不得 trim、按行拆分或插入空格。 |
| `reasoning_started` | 创建独立 reasoning 区块，不混入回答。 |
| `reasoning_delta` | 更新 reasoning 区块；可能只有计数而没有正文。 |
| `reasoning_finished` | 标记 reasoning 完成并允许折叠。 |
| `step` | 按 `data.type` 展示 Agent/工具进度。 |
| `model_retry_scheduled` | 显示自动重试状态，不要求用户 resume。 |
| `model_attempt_invalidated` | 把当前 attempt 草稿标记为 stale。 |
| `model_retry_exhausted` | 显示重试耗尽，等待后续终止事件。 |
| `tool_approval_required` | 记录审批请求，进入审批交互。 |
| `done` | 记录执行结论，但仍等待最终 RPC response。 |
| `error` | 展示安全错误和建议动作，但仍读取最终 RPC response。 |

未知事件和未知 payload 字段必须忽略并记录调试日志，不能让前端崩溃。

### 9.1 文本拼接

Token 是保真增量片段：

```python
answer_draft += event["data"]["content"]
```

禁止对 chunk 调用 `strip()`、使用 `splitlines()` 重组、自动追加换行，或把工具参数和 reasoning 当成回答文本。界面可以按固定帧率批量渲染，但缓冲区必须保存原始字符串。

### 9.2 完整消息兜底

若 Provider 没有产生 token，Core 可能发送：

```json
{"event":"step","data":{"type":"agent_message","content":"完整回答"}}
```

只有本轮尚未收到可见 token 时才显示该内容，否则会重复输出完整回答。

### 9.3 Reasoning

Reasoning 不加入回答 Markdown，也不写入用户可见历史。`metadata` 模式没有正文是正常行为；`redacted=true` 时不得尝试恢复原文。展开/折叠属于前端本地状态，不需要调用 RPC。

## 10. 完整服务端事件 JSON 目录

所有流式数据都放在 JSON-RPC notification 的 `params` 内：

```json
{
  "jsonrpc": "2.0",
  "method": "agent.event",
  "params": {
    "request_id": "原 JSON-RPC id",
    "run_id": "Core 生成的 run id",
    "event": "事件名称",
    "data": {}
  }
}
```

### 10.1 流式文本 `token`

```json
{
  "event": "token",
  "data": {"content": "增量文本", "attempt_id": "model-attempt-id"}
}
```

`attempt_id` 可选。回答文本只在 `data.content`，前端必须原样追加。

### 10.2 Thinking / reasoning

```json
{
    "event":"reasoning_started",
    "data":{
        "source":"parent_agent",
        "char_count":0,
        "redacted":false,
        "display":"collapsed",
        "expanded":false,
        "attempt_id":"attempt-id"
    }
}
{
    "event":"reasoning_delta",
    "data":{
        "source":"parent_agent",
        "content":"思考片段",
        "char_count":4,
        "redacted":false,
        "display":"collapsed",
        "expanded":false,
        "attempt_id":"attempt-id"
    }
}
{
    "event":"reasoning_finished",
    "data":{
        "source":"parent_agent",
        "char_count":856,
        "redacted":false,
        "display":"collapsed",
        "expanded":false,
        "attempt_id":"attempt-id"
    }
}
```

`content` 仅在 `collapsed/expanded` 配置且 Provider 提供原文时存在。`redacted=true` 时没有原文。

### 10.3 Agent 与工具步骤 `step`

```json
{"event":"step","data":{"type":"agent_start","message":"Agent turn started."}}
```

```json
{
  "event": "step",
  "data": {
    "type": "tool_call_start",
    "tool": "read_workspace_file_lite",
    "args": {"path": "README.md"},
    "id": "tool-call-id"
  }
}
```

```json
{
  "event": "step",
  "data": {
    "type": "tool_call_result",
    "tool": "read_workspace_file_lite",
    "tool_call_id": "tool-call-id",
    "content": "截断后的结果预览"
  }
}
```

```json
{"event":"step","data":{"type":"agent_message","content":"完整最终回答"}}
```

`args/content` 是显示预览，不保证包含完整工具输入输出。

### 10.4 权限请求 `tool_approval_required`

```json
{
  "event": "tool_approval_required",
  "data": {
    "request_id": "approval-id",
    "tool": "write_workspace_file",
    "args": {"path": "src/a.py", "content": "<120 chars omitted>"},
    "capabilities": ["file_write"],
    "persistable": true
  }
}
```

随后通常还有：

```json
{
  "event": "paused",
  "data": {
    "type": "tool_approval",
    "stop_reason": "tool_approval",
    "message": "Tool execution is waiting for approval.",
    "approval_request": {"request_id": "approval-id"},
    "tool_call_count": 1,
    "graph_steps_used": 2
  }
}
```

注意：内部 Slice 使用 `paused` 事件；RPC 聚合层最终会返回 `status="paused"`。前端应兼容两者。

### 10.5 模型自动重试

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

```json
{"event":"model_attempt_invalidated","data":{"attempt":1,"error_category":"timeout"}}
{"event":"model_retry_exhausted","data":{"attempt":3,"max_attempts":3,"error_category":"timeout"}}
```

### 10.6 成功终止 `done`

```json
{
  "event": "done",
  "data": {
    "run_id": "run-id",
    "status": "ok",
    "workspace_id": "workspace-uuid",
    "session_id": "session-uuid",
    "session_name": "default",
    "execution_id": "execution-id",
    "stop_reason": "completed",
    "tool_call_count": 2,
    "slices_used": 1,
    "goal_mode": true,
    "durability": "committed",
    "maintenance_status": "pending",
    "memory_status": "not_scheduled",
    "memory_request_explicit": false,
    "context_tokens": 11487
  }
}
```

### 10.7 暂停、空闲和归档 `done`

```json
{
  "event": "done",
  "data": {
    "status": "paused",
    "run_id": "run-id",
    "execution_id": "execution-id",
    "stop_reason": "graph_step_limit",
    "tool_call_count": 4,
    "slices_used": 1,
    "goal_mode": true,
    "message": "Execution paused."
  }
}
```

```json
{"event":"done","data":{"status":"idle","run_id":"run-id","workspace_id":"workspace-uuid","session_id":"session-uuid","session_name":"default","message":"Session has no pending execution to resume."}}
```

```json
{"event":"done","data":{"status":"archived","run_id":"run-id","workspace_id":"workspace-uuid","session_id":"session-uuid","session_name":"default","message":"Session is archived."}}
```

### 10.8 错误 `error`

```json
{
  "event": "error",
  "data": {
    "type": "provider_error",
    "stop_reason": "turn_error",
    "message": "安全的用户可见错误",
    "error_category": "rate_limited",
    "error_action": "pause",
    "retryable": true,
    "provider": "anthropic",
    "provider_code": "rate_limit_error",
    "http_status": 429,
    "request_id": "provider-request-id",
    "failure_source": "agent_turn",
    "failure_scope": "current_turn",
    "failure_stage": "parent_model_provider",
    "user_action": "resume_later",
    "graph_steps_used": 1
  }
}
```

错误字段按故障来源可能缺省；只有 `type/message/stop_reason` 应作为最低展示基线。

### 10.9 Token 用量当前到底在哪里

当前前端协议只稳定发送：

```json
{"event":"done","data":{"context_tokens":11487}}
```

`context_tokens` 表示最近一次成功 Turn 记录的模型输入上下文量。以下详细字段目前只写入 Trace/Telemetry，**不会作为统一 `agent.event` 发送给前端**：

```text
input_tokens
output_tokens
total_tokens
cache_creation_input_tokens
cache_read_input_tokens
provider request_id
```

因此前端目前不能可靠展示“本轮输入/输出/cache 命中”的详细统计。若产品需要这些指标，后端应新增严格的 `usage` 对象，例如：

```json
{
  "usage": {
    "input_tokens": 11487,
    "output_tokens": 232,
    "total_tokens": 11719,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 10240
  }
}
```

这是待实现契约，当前客户端不得假定它存在。
## 11. 工具调用与审批

普通工具进度通过 `step.tool_call_start` 和 `step.tool_call_result` 展示。字段是安全预览，不是完整工具输入输出接口，建议默认折叠。

```json
{
  "event": "tool_approval_required",
  "data": {
    "request_id": "approval-id",
    "tool": "write_workspace_file",
    "args": {"path": "src/example.py", "content": "<420 chars omitted>"},
    "capabilities": ["file_write"],
    "persistable": true
  }
}
```

前端必须：

1. 保存 approval `request_id`，不能用 JSON-RPC 请求 ID 替代。
2. 显示工具名、脱敏参数、能力和审批原因。
3. 若 `persistable=false`，只显示 `allow_once` 和 `deny_once`。
4. 等待用户明确选择；关闭窗口或 EOF 不得默认批准。
5. 调用 `approval.resolve`，而不是重新发送 `agent.chat`。

```json
{
  "workspace_root": "D:\\project",
  "session_name": "default",
  "request_id": "approval-id",
  "response": "allow_once"
}
```

`response` 可为 `allow_once`、`allow_session`、`allow_workspace`、`deny_once`、`deny_session` 或 `deny_workspace`。

`approval.resolve` 会恢复原 Execution，并可能再次发送 token、工具事件或新的审批请求，因此必须使用与 `agent.chat` 相同的事件消费循环。前端启动或断线恢复后可调用 `approval.list` 找回待审批请求。

## 12. 暂停、恢复与丢弃

`done.status=paused` 表示 Execution 没有完成。先调用 `session.status`：

```json
{
  "workspace_root": "D:\\project",
  "session_name": "default"
}
```

处理规则：

- `execution_recoverable=true`：显示 Resume 和 Discard。
- 存在待审批：优先走 `approval.list/resolve`。
- 普通可恢复暂停：调用 `session.resume`，可附加 `instruction`。
- 用户明确放弃：调用 `session.discard`。
- 不可恢复且已自动回滚：允许用户发送新的 `agent.chat`。

`session.resume` 是非幂等请求，也会产生完整的 `agent.event` 流。不要把 resume 实现成重新发送旧用户消息。

## 13. 断线恢复

连接在最终响应前中断时，不能判断 Core 是否已经执行模型或工具。禁止自动重发 `agent.chat`、`session.resume` 或 `approval.resolve`。

```text
connection interrupted
  -> mark visible draft incomplete
  -> reconnect with a new RPC connection
  -> session.status
     -> pending approval: approval.list
     -> recoverable execution: offer session.resume/discard
     -> no pending execution: allow a new chat
```

当前协议不支持事件游标和断线续传，断线前遗漏的 token 无法重新订阅。正式消息以 Core 持久化状态为准；重连后使用 `session.history` 恢复已提交消息，并用 `session.status` 判断是否存在待恢复 Execution。

## 14. 错误处理

### 14.1 传输错误

连接拒绝、EOF、超时、无效 UTF-8 或 JSON。前端应保留草稿并进入 `interrupted`，随后查询 `session.status`。

### 14.2 JSON-RPC 错误

```json
{
  "jsonrpc": "2.0",
  "id": "ui-8e0c7a",
  "error": {"code": -32602, "message": "Invalid params", "data": []}
}
```

- `-32001`：重新读取 token，必要时提示重启 daemon。
- `-32602`：客户端请求结构错误，不应重试原请求。
- `-32601`：客户端与 Core 版本不匹配。
- `-32603`：先查 Session 状态，不自动重发非幂等请求。

### 14.3 Agent `error` 事件

优先展示 Core 提供的安全 `message`。根据 `error_action`、`retryable`、`failure_stage` 和 `user_action` 生成操作。不要显示 API key、完整 Provider body、Prompt、工具完整输出或 traceback。

## 15. Session 管理

| 操作 | RPC |
|---|---|
| 查询当前 Session 状态 | `session.status` |
| 分页读取已提交历史 | `session.history` |
| 恢复暂停 Execution | `session.resume` |
| 丢弃暂停 Execution | `session.discard` |
| 归档或硬删除 Session | `session.delete` |
| 重建短期上下文缓存 | `session.reset` |

归档是默认删除语义；硬删除不可恢复，前端必须对 `hard_delete=true` 提供二次确认。

当前仍没有 Session 列表 RPC，但已可通过 `session.history` 加载已知 Session 的完整提交历史。
会话侧边栏需要另行增加 Session 列表接口；不得通过直接查询 SQLite 绕过这一限制。

## 16. Context 与 Token 用量

前端不得自行从 token chunk 推算权威上下文占用。启动时使用 `session.status` 的当前状态；一轮完成后使用 `done` 或最终结果中公开的用量字段更新界面。字段可能缺省，必须允许显示“未知”。

后台上下文摘要和长期记忆失败不等于当前对话失败。相关状态位于 `session.status.maintenance`，前端应明确标注为后台维护问题。

## 17. 安全要求

- daemon token 只保存在受信任的本地进程内，不进入 UI、日志或 Crash report。
- 工具参数和结果只按 Core 提供的脱敏预览显示。
- 文件写入审批不得展示完整文件正文。
- `allow_workspace` 是高影响授权，必须明确标注作用范围。
- 不允许网页直接连接 Core TCP。
- 不允许前端根据工具名自行绕过审批。
- Workspace 路径必须由用户明确选择，不应静默切换。

## 18. 兼容性规则

客户端必须忽略未知字段和未知事件，对可缺省字段使用空值处理，不依赖字典顺序或事件数量，也不能假设每轮一定有 token、工具、reasoning 或 execution ID。使用 `core.ping.server_version` 提示明显版本不匹配。

新增可选字段通常是兼容变更；删除字段、改变含义、改变终止顺序或新增必填参数属于破坏性变更。完整规则见[协议兼容性](/docs/api/protocol-compatibility.md)。

## 19. 推荐前端模块划分

```text
frontend/
  transport/ndjson_client
  protocol/rpc_models
  protocol/event_models
  application/chat_controller
  application/recovery_service
  state/conversation_store
  presentation/event_renderer
  presentation/approval_view
```

Transport 不决定 UI 文案，renderer 不发送 RPC，状态层不读取 Core 数据库。

## 20. 开发与验证清单

- 中文、空格和换行在 token 拼接后完全保真。
- 只收到完整 `agent_message` 时仍能显示回复。
- 模型重试后旧草稿标记 stale，不与新回答拼接。
- 工具审批允许、拒绝和再次审批均可继续同一 Execution。
- 断线后不自动重发非幂等请求。
- `session.status` 能驱动恢复和丢弃入口。
- `session.history` 能按完整 Turn 分页，前插旧页时保持用户视口。
- reasoning 与正式回答分离。
- 未知事件和未知字段不会导致崩溃。
- token、工具正文和敏感参数不会写入客户端日志。

仓库参考实现：同步客户端 `src/cli/client.py`、异步客户端 `src/tui/client.py`、TUI 编排 `src/tui/screens/chat.py`、事件渲染 `src/tui/renderer.py`。这些文件是实现参考，不是独立于 API 文档的稳定契约。

## 21. 当前缺口

- Session 列表查询。
- 执行中取消。
- 事件序号、断线续传和 Run 重新订阅。
- 同一连接多请求并发。
- 多客户端订阅同一个 Run。
- HTTP/WebSocket browser gateway。
- 可生成 TypeScript 类型的完整 JSON Schema/OpenAPI。
- 协议能力协商和正式版本握手。

前端设计必须对这些限制显式降级。若要支持完整 Web/桌面产品，应优先补齐公开查询接口、严格响应模型、事件联合类型和可生成的协议 Schema，而不是让前端依赖数据库结构。

## 资源活动接入

前端在收到 `resource_activity_summary` 时可显示本 Turn 的读取量、文件变更和证据警告。
需要展开文件列表或恢复历史视图时调用 `resource_activity.list`，不要解析 Tool 文本结果，也不要读取本地数据库。
`resource_uri` 是稳定展示标识；`proposed` 不是实际 Workspace 变更，只有 `applied` 才应进入已变更文件区域。
