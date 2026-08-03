# RPC 方法参考

> 文档状态：Current
> 权威范围：Core daemon 当前公开的 RPC 方法、参数和结果
> 维护触发：注册、删除或修改 RPC 方法

## 本文负责

- Core 当前公开 RPC 的方法、参数、结果、幂等性和重试风险。

## 本文不负责

- 不定义 TCP/NDJSON 分帧；见 IPC 协议。
- 不解释 handler 或业务服务内部实现。


本文记录当前 Core daemon 对外公开的方法。所有参数模型均采用严格校验，未知字段会被拒绝。

所有请求都必须包含：

```json
{
  "jsonrpc": "2.0",
  "id": "client-generated-id",
  "method": "method.name",
  "params": {
    "auth_token": "daemon-token"
  }
}
```

## 方法总览

| 方法 | 用途 | 幂等性 | 可自动重试 |
|---|---|---:|---:|
| `core.ping` | daemon 健康检查 | 是 | 是 |
| `core.shutdown` | 请求优雅关闭 | 基本幂等 | 谨慎 |
| `agent.chat` | 发起新一轮对话 | 否 | 否 |
| `session.status` | 查询待恢复 Execution | 是 | 是 |
| `session.history` | 按完整 Turn 分页读取已提交历史 | 是 | 是 |
| `session.resume` | 恢复待执行任务 | 否 | 否 |
| `session.discard` | 丢弃待恢复 Execution | 基本幂等 | 谨慎 |
| `session.delete` | 归档或硬删除 Session | 基本幂等 | 谨慎 |
| `session.reset` | 从归档消息重建 recent_messages | 是 | 是 |
| `approval.list` | 查询当前 Session 待审批工具调用 | 是 | 是 |
| `approval.resolve` | 审批并恢复原工具调用 | 否 | 否 |
| `approval.mode.get` | 查询全局、Session override 与有效审批模式 | 是 | 是 |
| `approval.mode.set` | 设置 Session 审批模式或恢复继承 | 基本幂等 | 谨慎 |
| `resource_activity.summary` | 查询 Execution 或历史 Turn 的资源活动聚合 | 是 | 是 |
| `resource_activity.list` | 游标分页查询资源活动明细 | 是 | 是 |
“不可自动重试”表示连接中断时请求可能已经在 Core 内执行。直接重发可能造成重复模型调用、工具调用或消息写入。

## `core.ping`

参数只有 `auth_token`。

成功结果：

```json
{
  "status": "ok",
  "server_version": "0.1.0",
  "uptime_ms": 12345
}
```

该方法不访问 Agent、Workspace 或数据库业务状态。

## `core.shutdown`

参数只有 `auth_token`。

成功结果：

```json
{"status":"shutting_down"}
```

Core 先写回最终响应，再开始优雅关闭。客户端不应假设收到响应时进程已经退出。

## `agent.chat`

参数：

| 字段 | 类型 | 限制 | 含义 |
|---|---|---|---|
| `workspace_root` | string | 1..4000 字符 | 当前工作区根目录 |
| `session_name` | string | 1..200 字符，默认 `default` | Workspace 内 Session 名称 |
| `message` | string | 1..200000 字符 | 用户输入 |
| `goal_mode` | boolean | 默认 `false` | 是否启用父 Agent 私有任务规划工具 |

示例：

```json
{
  "jsonrpc": "2.0",
  "id": "chat-1",
  "method": "agent.chat",
  "params": {
    "auth_token": "...",
    "workspace_root": "D:\\project",
    "session_name": "default",
    "message": "分析当前项目结构",
    "goal_mode": false
  }
}
```

`goal_mode=true` 适合复杂目标，会让父 Agent 获得 `task_plan`、`task_update`、`task_list` 和 `task_get` 私有工具。该字段不是任务 API；客户端不能直接管理任务，只能选择本轮是否以 goal 模式启动。若 goal Execution 因预算暂停，后续 `session.resume` 会继承该 Execution 的 goal 模式，不需要客户端再次传入该字段。

执行期间会产生 `agent.event` 通知。最终结果通常包含：

```json
{
  "run_id": "...",
  "status": "ok",
  "workspace_id": "...",
  "session_id": "...",
  "session_name": "default",
  "execution_id": "...",
  "stop_reason": "completed",
  "tool_call_count": 2,
  "slices_used": 1,
  "durability": "committed",
  "maintenance_status": "pending",
  "memory_status": "pending"
}
```

字段可能随执行模式而缺省，例如无模型配置的诊断 Turn 没有 `execution_id`。

## `session.status`

参数：

```json
{
  "auth_token": "...",
  "workspace_root": "D:\\project",
  "session_name": "default"
}
```

结果包含 Session 身份、待恢复 Execution、checkpoint 状态和后台维护任务计数：

```json
{
  "workspace_id": "...",
  "session_id": "...",
  "session_name": "default",
  "pending_execution": null,
  "execution_recoverable": false,
  "checkpoint_state": null,
  "maintenance": {
    "pending": 0,
    "running": 0,
    "failed": 0,
    "recent_failures": []
  },
  "tool_approval": {
    "default_mode": "manual",
    "override_mode": null,
    "effective_mode": "manual",
    "supported_modes": ["manual", "accept_all"],
    "pending_count": 0
  }
}
```

`maintenance.recent_failures` 返回最近几个后台维护失败任务。后台维护任务包括上下文摘要压缩、
长期记忆提取和 checkpoint 清理。它们可能调用 LLM，但不属于当前前台对话请求；如果这里出现
`job_type=context_summary` 或 `job_type=memory_extract`，前端应提示这是后台派生任务失败，而不是
本轮用户输入导致当前对话失败。

`tool_approval` 与 `approval.mode.get` 使用同一结构，供前端在启动、Session 切换或 daemon 重连后恢复状态栏和审批入口；它不替代 `approval.list` 的请求明细。

如果 Session 已被归档，`session.status` 返回 `status=archived`，不会自动创建同名新 Session。

所有 `session.*` 方法会把无效 Workspace 路径、空名称或操作所需 Session 不存在等领域输入错误返回为
JSON-RPC `-32602 Invalid params`，不会伪装成服务端内部错误。`approval.list/resolve` 对无效 Session、
审批请求不存在或无法恢复对应 Execution 使用相同错误契约。数据库锁、I/O 和程序缺陷仍返回内部错误，
客户端不应把它们当成参数问题自动改写请求。

## `session.history`

返回指定 Session 已经提交的消息历史，不返回暂停 Execution 中尚未提交的回答草稿。不存在的
Session 返回空页，归档 Session 仍可读取。

参数：

| 字段 | 类型 | 限制 | 含义 |
|---|---|---|---|
| `workspace_root` | string | 1..4000 字符 | Workspace 根目录 |
| `session_name` | string | 1..200 字符 | Session 名称 |
| `before_turn` | integer/null | 大于等于 0 | 排他游标，只返回更早的 Turn |
| `limit_turns` | integer | 1..100，默认 30 | 本页最多返回的完整 Turn 数 |

响应按 `turn_index` 正序排列，分页不会截断同一 Turn 内的
`tool_use -> tool_result -> final answer`：

```json
{
  "schema_version": 1,
  "session_name": "default",
  "archived": false,
  "turns": [
    {
      "turn_index": 12,
      "messages": [
        {
          "message_id": "...",
          "role": "assistant",
          "message_type": "AIMessage",
          "blocks": [
            {"type": "reasoning", "char_count": 856, "display": "collapsed", "redacted": false},
            {"type": "text", "text": "最终回答"}
          ]
        }
      ]
    }
  ],
  "next_before_turn": 12,
  "has_more": true
}
```

`blocks` 支持 `text`、`reasoning`、`tool_call` 和 `tool_result`。reasoning 是否包含受限正文由
`LEARN_AGENT_REASONING_DISPLAY` 决定；redacted thinking 永不返回正文。工具参数经过脱敏和截断，
工具结果只返回安全预览。响应不包含数据库 `raw`、thinking signature、完整文件正文或密钥。

活动分支存在时，Core 从 branch head 沿 `parent_message_id` 读取祖先链，因此分支历史包含分叉点
之前的上下文，但不混入其他分支。旧 Session 或无效 branch head 会回退到
`turn_index, message_ordinal` 顺序。

`limit_turns` 是数量上限，不保证响应一定包含这么多 Turn。为了让一条 UTF-8 NDJSON 响应保持在
Core 的消息帧限制内，服务端还会按序列化字节预算减少本页数量，但只会移除完整 Turn。此时
`has_more=true`，客户端应继续使用 `next_before_turn` 请求更早历史，不能通过增大
`limit_turns` 假设可以绕过帧限制。

## `session.resume`

参数与 `session.status` 相同，并可增加：

```json
{"instruction":"继续执行，但先运行测试"}
```

该方法会产生与 `agent.chat` 相同的流式通知，并创建新的 `run_id`，但继续使用原有 `execution_id`。

## `session.discard`

丢弃当前 Session 绑定的待恢复 Execution，但保留审计记录。成功结果：

```json
{"status":"discarded","execution_id":"..."}
```

## `session.delete`

默认归档指定 Session，使其不可继续 `chat/resume/discard`，但保留历史消息、Execution、任务计划和维护记录。
归档适合隐藏不再使用的 Session，同时保留审计与排障数据。

参数：

```json
{
  "workspace_root": "D:\\project",
  "session_name": "default",
  "hard_delete": false
}
```

成功归档结果：

```json
{"status":"archived","mode":"archive","session_name":"default"}
```

设置 `hard_delete=true` 会永久删除该 Session，并依赖本地 SQLite 外键级联删除与它绑定的消息、分支、
Execution、任务计划和维护任务。该操作不可恢复，应只用于明确不再需要历史数据的场景。

```json
{"status":"deleted","mode":"hard_delete","session_name":"default"}
```

checkpoint 删除通过后台维护任务完成，因此返回成功不表示 checkpoint 文件已立即清理。

## `session.reset`

从 `messages` 表的归档消息中重建 `sessions` 表的 `recent_messages` 缓存，并将 `context_tokens` 重置为 0。这是一个恢复操作用于解决 `recent_messages` 中包含导致 LLM 供应商拒绝请求的内容时出现的"卡死"问题。

参数与 `session.status` 相同：

```json
{
  "auth_token": "...",
  "workspace_root": "D:\\project",
  "session_name": "default"
}
```

成功结果：

```json
{
  "status": "ok",
  "workspace_id": "...",
  "session_id": "...",
  "session_name": "default",
  "recovered_messages": 12
}
```

`recovered_messages` 表示从归档消息中恢复了多少条消息到 `recent_messages`。如果 Session 已被归档，返回 `status=archived` 且 `recovered_messages=0`。

**恢复原理**：`recent_messages` 是 `sessions` 行中的历史兼容 JSON 缓存列，当前保存最近 N 个完整 Turn 用于构建 LLM 输入（N 由 `RECENT_TURN_LIMIT` 控制）。一个 Turn 可以包含多条 message，例如用户输入、AI 回复和工具结果。当缓存异常或需要重建时，`session.reset` 会从 `messages.raw` 按完整 Turn 边界恢复近期上下文。

## `approval.list`

`approval.list` 使用与 `session.status` 相同的 Workspace 和 Session 参数，返回尚未处理的工具审批请求。请求只包含脱敏、截断后的参数摘要。

## `approval.resolve`

`approval.resolve` 额外接收：

```json
{"request_id":"approval-id","response":"allow_once"}
```

`response` 可为 `allow_once`、`allow_session`、`allow_workspace`、`deny_once`、`deny_session` 或 `deny_workspace`。该方法通过 LangGraph checkpoint 恢复原工具调用，不会重新开始整个 Turn。可可靠解析的复合 shell 命令使用完整调用的 SHA-256 精确规则，无法解析的 shell 语法只允许单次审批。

## `approval.mode.get`

使用与 `session.status` 相同的 Workspace 和 Session 参数，返回：

```json
{
  "schema_version": 1,
  "default_mode": "manual",
  "override_mode": null,
  "effective_mode": "manual",
  "supported_modes": ["manual", "accept_all"],
  "pending_count": 0
}
```

`override_mode=null` 表示继承 daemon 全局默认。`supported_modes` 来自服务端策略注册表，前端不能写死只有两个模式。未知持久值会安全回退为 `manual`。

## `approval.mode.set`

额外接收 `mode`，其值为服务端支持的模式名或 `inherit`。切换到 `accept_all` 时还必须传 `acknowledge_risk=true`：

```json
{"mode":"accept_all","acknowledge_risk":true}
```

响应与 `approval.mode.get` 相同，并增加 `existing_pending_unchanged`。模式切换只影响之后创建的请求；现有 pending 不会被自动执行。`accept_all` 只把策略产生的 `ASK` 记为自动 `allow_once`，不能覆盖 `DENY`、Hook 拒绝或路径、沙箱、网络等硬边界。

## 结果兼容规则

当前结果对象尚未全部建模为严格 Pydantic 类型。客户端应：

- 依赖本文列出的稳定核心字段。
- 忽略未知字段。
- 对标记为“可能缺省”的字段使用空值处理。
- 不依赖字典字段顺序。

## Resource Activity

### `resource_activity.summary`

返回版本化的 Agent Turn 资源活动聚合。参数必须提供 `execution_id`，或者同时提供
`workspace_root`、`session_name` 和 `turn_index`。历史 Turn 查询只查找已登记的 Workspace 和 Session，
不会注册 Workspace、刷新 `updated_at` 或产生其他状态写入。结果包含读取资源数、返回字节、
实际及暂存变更数量、读取证据状态和 `truncated` 标志。`changes.applied` 统计逻辑变更数；
一次 MOVE 是一个逻辑变更，但 `changed_resource_count` 会把源 URI 和目标 URI 都计为受影响资源。
该接口是 Web、桌面端、IDE、CLI 与 TUI 的统一查询边界，客户端不得直接读取 `state.db`。

### `resource_activity.list`

按相同 scope 返回安全元数据明细。可用 `operation`、`change_state`、`resource_uri` 过滤，
并通过 `cursor`、`limit` 进行基于 Execution 内 `sequence` 的稳定分页。查询在一个 SQLite
只读快照内完成。返回 `schema_version`、`items`、`next_cursor` 和 `has_more`；不包含文件正文、
完整命令输出或宿主机绝对路径。损坏的可选 JSON 元数据按 `null` 或空列表降级，不会使整页查询失败。
