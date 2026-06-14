# RPC 方法参考
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
    "message": "分析当前项目结构"
  }
}
```

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
  "maintenance": {"pending": 0, "running": 0, "failed": 0}
}
```

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

checkpoint 删除通过后台维护任务完成，因此返回成功不表示 checkpoint 文件已立即清理。

## 结果兼容规则

当前结果对象尚未全部建模为严格 Pydantic 类型。客户端应：

- 依赖本文列出的稳定核心字段。
- 忽略未知字段。
- 对标记为“可能缺省”的字段使用空值处理。
- 不依赖字典字段顺序。
