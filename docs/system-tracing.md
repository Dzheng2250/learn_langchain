# 系统级 Trace 时间线

## 1. Trace 解决什么问题

项目已经有两种记录机制：

- `state.db` 保存 Session、Execution、消息和恢复状态，是业务事实的权威来源。
- Telemetry Event 保存 `turn_started`、`tool_finished` 等领域事件，用于指标和错误观测。

它们仍然不能完整回答一次请求为什么失败或变慢。例如：

- CLI 请求是否成功进入 Core？
- JSON-RPC 在验证、鉴权还是 Handler 中失败？
- Agent 的某个 Slice 调用了几次 LLM，模型分别耗时多久？
- 响应已经生成，但是否成功写回客户端？

System Trace 将 IPC、Agent、LLM、Tool、Telemetry 和 Core 生命周期记录到同一条按时间排序的
JSONL 时间线。Trace 是排障资料，不参与任务恢复，也不能作为计费或审计依据。

```text
Execution State = 系统现在必须相信什么
Telemetry Event = 业务中发生了什么
System Trace    = 数据经过各层时发生了什么
```

## 2. 存储位置与生命周期

默认位置：

```text
<user_data_dir>/learn-agent/state/traces/YYYY-MM-DD/daemon.jsonl
```

Windows 通常类似：

```text
C:\Users\<user>\AppData\Local\learn-agent\state\traces\2026-06-14\daemon.jsonl
```

规则：

- 使用 UTC 日期分目录，每日自动轮转。
- 默认保留 14 天，Core 启动时清理更早的完整日期目录。
- `LEARN_AGENT_STATE_DIR` 会改变整个本地状态根目录。
- `LEARN_AGENT_TRACE_DIR` 只覆盖 Trace 目录。
- Trace Writer 使用有界内存队列和后台线程；文件 IO 不进入 token 输出与 JSON-RPC 响应关键路径。
- 队列满、写入失败或进程被强制终止时允许丢失 Trace，但不能影响 Agent 业务结果。

## 3. TraceRecord 模型

每行 JSON 都是一个不可变 `TraceRecord`：

| 字段 | 含义 |
|---|---|
| `schema_version` | Trace 行格式版本，便于未来兼容读取 |
| `daemon_id` | 当前 Core 进程 ID，区分 daemon 重启 |
| `sequence` | 当前 daemon 内严格递增的入队序号 |
| `timestamp` | UTC 墙上时间，便于人类阅读和跨系统对照 |
| `monotonic_ns` | 单进程内单调时间，避免系统时钟调整影响耗时计算 |
| `direction` | 数据流向，例如 `CLIENT_TO_CORE`、`PROVIDER_TO_CORE` |
| `layer` | 事件所在层，例如 `ipc`、`agent`、`llm` |
| `kind` | 稳定事件名，例如 `ipc.response_sent` |
| `trace_id` | 一次端到端 JSON-RPC 请求的关联 ID |
| `request_id` | JSON-RPC 请求 ID |
| `run_id` | 一次 `agent.chat` 或 `session.resume` 调用 |
| `execution_id` | 可跨多个 Run 恢复的长期任务 |
| `slice_id` | 一次有步数限制的 LangGraph 执行片段 |
| `span_id` / `parent_span_id` | 当前操作及父操作，为未来生成调用树预留 |
| `client_id` | 当前本地 TCP 客户端摘要 |
| `duration_ms` | 已完成操作的耗时 |
| `data` | 经过脱敏、截断的开放摘要字段 |

### 为什么同时需要多个 ID

这些 ID 描述不同生命周期：

```text
一个 Execution
  -> 第一次 chat 形成 Run A
       -> Slice 1
       -> Slice 2，达到预算后暂停
  -> 用户 resume 形成 Run B
       -> Slice 3，任务完成
```

- 查询一次客户端请求使用 `trace_id` 或 `request_id`。
- 查询一次 chat/resume 使用 `run_id`。
- 查询任务跨多次恢复的完整历史使用 `execution_id`。
- 查询某次受限图执行使用 `slice_id`。

只保留 `run_id` 无法重建跨 resume 的任务；只保留 `execution_id` 又无法区分不同客户端请求。

## 4. 数据流与设计模式

```text
Producer
  -> record_trace()
  -> TraceRecorder
       -> 分配 daemon_id + sequence
       -> 合并 TraceContext
       -> 脱敏和截断
  -> TraceWriter.emit()
  -> BoundedBatchWorker.put_nowait()
  -> 后台批量写入每日 daemon.jsonl
```

采用的设计模式：

- **Decorator**：`TracingModelProvider` 包装任意 `ModelProvider`，不修改具体服务商实现。
- **Adapter**：`TelemetryTraceSink` 将现有 Telemetry Event 转为安全 Trace 摘要。
- **Context Propagation**：`ContextVar` 传播 `trace_id/run_id/execution_id/slice_id`。
- **Composition Root**：只有 `CoreApp` 创建、安装和关闭 Trace 资源。
- **Producer/Consumer**：业务线程只入队，后台 Writer 批量执行文件 IO。
- **Best-effort Sink**：Trace 失败与业务失败隔离。

`BoundedBatchWorker` 同时被 Telemetry 和 Trace 使用，统一处理有界队列、批量写入、丢弃和关闭。
项目没有建立第二套 EventBus；Trace 是跨层记录器，Telemetry 只是它的一个数据来源。

## 5. 当前埋点

### IPC

| Trace | 产生位置 | 语义 |
|---|---|---|
| `ipc.request_received` | `SocketServer` | 成功解析一条 NDJSON JSON 值 |
| `ipc.request_validated` | `RpcRouter` | 协议、参数与鉴权均通过 |
| `ipc.request_rejected` | Transport / Router | Parse Error、无效参数、未知方法或鉴权失败 |
| `ipc.notification_sent` | `SocketRequestContext` | `writer.drain()` 后确认通知已写入连接 |
| `ipc.response_sent` | `SocketRequestContext` | `writer.drain()` 后确认最终响应已写入连接 |
| `ipc.connection_interrupted` | `SocketServer` | 连接被中断或请求任务取消 |

### Agent、Execution 与 Slice

```text
agent.execution_attached
agent.run_started
agent.slice_started
agent.slice_finished
agent.run_paused
agent.run_finished
agent.run_failed
```

Agent Turn 运行在线程池中。提交 worker 前使用 `contextvars.copy_context()`，因此 IPC 创建的
`trace_id/request_id/run_id` 会正确传播到 worker，随后再补充 `execution_id` 和 `slice_id`。

### LLM

`TracingModelProvider` 为 LangChain Runnable 注入 callback：

```text
llm.request_started
llm.response_finished
llm.request_failed
```

默认只记录模型、用途、消息数量、工具数量、耗时、停止原因，以及服务商实际返回的 Token 数。
服务商没有返回 Token usage 时保存 `null`，不会额外估算。

### Tool 与 Telemetry

现有 `ObservedToolNode` 继续集中产生 Tool Telemetry。`TelemetryTraceSink` 仅复制白名单摘要，
例如工具名、调用 ID、结果字符数和耗时，不复制完整参数、文件内容或工具结果。

子 Agent 视为一次 Tool 调用；Trace 不保存其临时内部会话。

## 6. 安全边界

Trace 默认禁止保存：

```text
auth_token / API key / password / secret
完整用户消息
完整 Prompt 和消息数组
完整模型响应
完整工具参数与结果
文件内容和 .env 内容
```

`data` 在进入 Writer 前递归清洗并限制字符串长度。IPC 只记录参数字段名，LLM 只记录数量和
usage，Telemetry 只允许白名单字段。

即使 Trace 只保存摘要，它仍可能包含项目名称、工具名和错误类型等信息。Trace 目录应视为本地
诊断数据，不应公开上传。

## 7. 查询

```shell
learn-agent trace
learn-agent trace --run <run_id>
learn-agent trace --execution <execution_id>
learn-agent trace --layer llm
learn-agent trace --direction CORE_TO_PROVIDER
learn-agent trace --kind llm.response_finished
learn-agent trace --follow
learn-agent trace --raw
learn-agent trace --limit 200
```

- 默认读取保留期内所有日期文件，并显示最近 200 条匹配记录。
- 多个过滤条件采用 AND 关系。
- `--follow` 从当天文件末尾开始持续读取，并在 UTC 日期变化后切换文件。
- `--raw` 输出原始 JSONL，便于交给 `jq` 等工具处理。
- CLI 直接读取用户级文件，不要求 daemon 正常运行。

## 8. 当前边界与未来方向

当前 Trace 是本地、单 daemon、best-effort 时间线：

- 不支持 OpenTelemetry 导出。
- 不支持跨机器分布式追踪。
- 不支持完整 Payload 调试模式。
- `span_id/parent_span_id` 已预留，但当前 CLI 尚不生成调用树。
- Trace 丢失不能用于判断业务是否提交成功；应查询 `state.db` 和 Session 状态。
- Token usage 仅记录服务商返回的数据，不能作为精确计费来源。

未来可在不修改业务模块的情况下增加 OpenTelemetry Adapter、调用树分析器或更丰富的 CLI
耗时统计。
