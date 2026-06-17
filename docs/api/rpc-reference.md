# RPC 方法参考

> 文档状态：Current
> 权威范围：Core daemon 当前公开的 RPC 方法、参数和结果
> 维护触发：注册、删除或修改 RPC 方法

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
| `session.resume` | 恢复待执行任务 | 否 | 否 |
| `session.discard` | 丢弃待恢复 Execution | 基本幂等 | 谨慎 |
| `session.delete` | 归档或硬删除 Session | 基本幂等 | 谨慎 |
| `session.reset` | 从归档消息重建 recent_messages | 是 | 是 |

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
  }
}
```

`maintenance.recent_failures` 返回最近几个后台维护失败任务。后台维护任务包括上下文摘要压缩、
长期记忆提取和 checkpoint 清理。它们可能调用 LLM，但不属于当前前台对话请求；如果这里出现
`job_type=context_summary` 或 `job_type=memory_extract`，前端应提示这是后台派生任务失败，而不是
本轮用户输入导致当前对话失败。

如果 Session 已被归档，`session.status` 返回 `status=archived`，不会自动创建同名新 Session。

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

**恢复原理**：`recent_messages` 是 `sessions` 行中的一个 JSON 列，缓存最近 N 条消息用于构建 LLM 输入（N 由 `RECENT_MESSAGE_LIMIT` 控制）。正常情况下每次 turn 提交时它与 `messages` 表同步写入。当 `recent_messages` 因供应商内容审查或其他异常进入自循环失败时，`session.reset` 从 `messages` 表的 `raw` 列反序列化恢复这些消息，切断循环。受影响的 session 不需要删除重建。

## 结果兼容规则

当前结果对象尚未全部建模为严格 Pydantic 类型。客户端应：

- 依赖本文列出的稳定核心字段。
- 忽略未知字段。
- 对标记为“可能缺省”的字段使用空值处理。
- 不依赖字典字段顺序。
