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

## 5. 最小客户端实现

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

## 6. 前端状态模型

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

## 7. 发起普通对话

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

## 8. 流式事件处理

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

### 8.1 文本拼接

Token 是保真增量片段：

```python
answer_draft += event["data"]["content"]
```

禁止对 chunk 调用 `strip()`、使用 `splitlines()` 重组、自动追加换行，或把工具参数和 reasoning 当成回答文本。界面可以按固定帧率批量渲染，但缓冲区必须保存原始字符串。

### 8.2 完整消息兜底

若 Provider 没有产生 token，Core 可能发送：

```json
{"event":"step","data":{"type":"agent_message","content":"完整回答"}}
```

只有本轮尚未收到可见 token 时才显示该内容，否则会重复输出完整回答。

### 8.3 Reasoning

Reasoning 不加入回答 Markdown，也不写入用户可见历史。`metadata` 模式没有正文是正常行为；`redacted=true` 时不得尝试恢复原文。展开/折叠属于前端本地状态，不需要调用 RPC。

## 9. 工具调用与审批

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

## 10. 暂停、恢复与丢弃

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

## 11. 断线恢复

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

当前协议不支持事件游标和断线续传，断线前遗漏的 token 无法重新订阅。正式消息以 Core 持久化状态为准，但当前尚无公开的 Session 历史查询 RPC。

## 12. 错误处理

### 12.1 传输错误

连接拒绝、EOF、超时、无效 UTF-8 或 JSON。前端应保留草稿并进入 `interrupted`，随后查询 `session.status`。

### 12.2 JSON-RPC 错误

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

### 12.3 Agent `error` 事件

优先展示 Core 提供的安全 `message`。根据 `error_action`、`retryable`、`failure_stage` 和 `user_action` 生成操作。不要显示 API key、完整 Provider body、Prompt、工具完整输出或 traceback。

## 13. Session 管理

| 操作 | RPC |
|---|---|
| 查询当前 Session 状态 | `session.status` |
| 恢复暂停 Execution | `session.resume` |
| 丢弃暂停 Execution | `session.discard` |
| 归档或硬删除 Session | `session.delete` |
| 重建短期上下文缓存 | `session.reset` |

归档是默认删除语义；硬删除不可恢复，前端必须对 `hard_delete=true` 提供二次确认。

当前没有 Session 列表和消息历史查询 RPC。因此通用 GUI 还不能只靠公开协议实现“会话侧边栏”和“加载完整历史”。不得通过直接查询 SQLite 绕过这一限制，应先扩展公开 RPC。

## 14. Context 与 Token 用量

前端不得自行从 token chunk 推算权威上下文占用。启动时使用 `session.status` 的当前状态；一轮完成后使用 `done` 或最终结果中公开的用量字段更新界面。字段可能缺省，必须允许显示“未知”。

后台上下文摘要和长期记忆失败不等于当前对话失败。相关状态位于 `session.status.maintenance`，前端应明确标注为后台维护问题。

## 15. 安全要求

- daemon token 只保存在受信任的本地进程内，不进入 UI、日志或 Crash report。
- 工具参数和结果只按 Core 提供的脱敏预览显示。
- 文件写入审批不得展示完整文件正文。
- `allow_workspace` 是高影响授权，必须明确标注作用范围。
- 不允许网页直接连接 Core TCP。
- 不允许前端根据工具名自行绕过审批。
- Workspace 路径必须由用户明确选择，不应静默切换。

## 16. 兼容性规则

客户端必须忽略未知字段和未知事件，对可缺省字段使用空值处理，不依赖字典顺序或事件数量，也不能假设每轮一定有 token、工具、reasoning 或 execution ID。使用 `core.ping.server_version` 提示明显版本不匹配。

新增可选字段通常是兼容变更；删除字段、改变含义、改变终止顺序或新增必填参数属于破坏性变更。完整规则见[协议兼容性](/docs/api/protocol-compatibility.md)。

## 17. 推荐前端模块划分

```text
frontend/
  transport/ndjson_client
  protocol/rpc_models
  protocol/event_models
  application/chat_controller
  application/recovery_service
  state/conversation_store
  presentation/event_renderer
  presentation/approval_dialog
```

Transport 不决定 UI 文案，renderer 不发送 RPC，状态层不读取 Core 数据库。

## 18. 开发与验证清单

- 中文、空格和换行在 token 拼接后完全保真。
- 只收到完整 `agent_message` 时仍能显示回复。
- 模型重试后旧草稿标记 stale，不与新回答拼接。
- 工具审批允许、拒绝和再次审批均可继续同一 Execution。
- 断线后不自动重发非幂等请求。
- `session.status` 能驱动恢复和丢弃入口。
- reasoning 与正式回答分离。
- 未知事件和未知字段不会导致崩溃。
- token、工具正文和敏感参数不会写入客户端日志。

仓库参考实现：同步客户端 `src/cli/client.py`、异步客户端 `src/tui/client.py`、TUI 编排 `src/tui/screens/chat.py`、事件渲染 `src/tui/renderer.py`。这些文件是实现参考，不是独立于 API 文档的稳定契约。

## 19. 当前缺口

- Session 列表与完整消息历史查询。
- 执行中取消。
- 事件序号、断线续传和 Run 重新订阅。
- 同一连接多请求并发。
- 多客户端订阅同一个 Run。
- HTTP/WebSocket browser gateway。
- 可生成 TypeScript 类型的完整 JSON Schema/OpenAPI。
- 协议能力协商和正式版本握手。

前端设计必须对这些限制显式降级。若要支持完整 Web/桌面产品，应优先补齐公开查询接口、严格响应模型、事件联合类型和可生成的协议 Schema，而不是让前端依赖数据库结构。