# 非功能性需求

> 文档状态：Current
> 权威范围：延迟、可靠性、一致性、安全和可维护性目标
> 维护触发：用户体验目标、关键路径、可靠性或安全边界变化

数据库事务、维护任务、CAS、Saga 和恢复协调器的定义及实现边界见
[`/docs/architecture/database-state-and-consistency.md`](/docs/architecture/database-state-and-consistency.md)。

本文定义 Learn Agent 除功能正确性之外必须满足的质量要求。功能测试通过不代表这些要求
已经满足；每项要求必须通过可重复的性能或故障注入测试验收。

## 1. 设计原则

### 1.1 用户输出优先

一旦模型产生可展示内容，任何持久化不得延迟 token 输出。模型完成后，完整消息和 Session
最小业务状态必须经过一次本地原子提交后才能返回成功；摘要、Telemetry、长期记忆和 checkpoint
清理不得延迟 CLI 恢复输入。

用户不应看到以下基础设施状态：

```text
正在保存会话...
正在写入数据库...
正在记录事件...
```

这些操作属于 Core 内部职责。只有需要用户决策或功能语义要求确认时，才允许显示状态。

### 1.2 前台与后台边界

| 工作 | 是否允许阻塞 token 输出 | 是否允许延迟 CLI 恢复输入 |
|---|---|---|
| LLM 和工具执行 | 是，属于回答生成过程 | 是 |
| Socket token 发送 | 是，但必须有超时和背压边界 | 是 |
| 完整消息与最小 Session 状态原子提交 | 否 | 是，作为耐久性屏障 |
| Telemetry 数据库写入 | 否 | 否 |
| JSONL/Console Telemetry | 否 | 否 |
| 普通长期记忆提取 | 否 | 否 |
| 上下文压缩 | 不得影响已产生 token | 否；通过持久化维护任务后台执行 |
| 用户明确要求“记住” | 不得影响已产生 token | 否；返回 pending，不虚假确认成功 |

最小业务提交允许占用最后一个 token 到最终响应之间的短暂窗口，但不得影响已经开始的 token
流。所有可重建的派生维护只能影响后台最终一致性。

### 1.3 同一 Session 一致性

后台持久化不能导致下一轮读取旧状态。目标行为：

1. 上一轮最小业务提交成功后，CLI 立即恢复输入。
2. 摘要、记忆与 checkpoint 清理在后台执行。
3. 用户立即发送同一 Session 下一轮时，只依赖上一轮已完成的最小提交。
4. 不同 Session 不受后台维护影响。
5. 派生维护失败必须记录并有限重试，不能静默丢失。

## 2. 延迟目标

以下指标以本地 Core、健康本地状态库、可选 PostgreSQL Telemetry 关闭或健康、且未包含模型服务
自身响应时间为基准。测试阈值用于发现架构回归，不代表公网模型调用的服务承诺。

| 指标 | 定义 | 目标 |
|---|---|---:|
| `stream_forward_latency_ms` | Core 收到 token 到开始写入客户端连接 | p95 < 20 ms，p99 < 50 ms |
| `telemetry_publish_latency_ms` | 业务线程调用 `emit_event()` 的耗时 | p95 < 2 ms，p99 < 10 ms |
| `trace_record_latency_ms` | 任意业务线程调用 `record_trace()` 的耗时 | p95 < 2 ms，p99 < 10 ms |
| `response_release_latency_ms` | 最后一个可见 token 到 CLI 可再次接受输入 | p95 < 100 ms，p99 < 250 ms |
| `minimal_commit_latency_ms` | 消息、Session、Execution 和维护任务原子提交耗时 | p95 < 100 ms；是响应前耐久性屏障 |
| `same_session_handoff_ms` | 下一轮解析已提交上一轮状态的额外耗时 | p95 < 50 ms |
| `different_session_interference_ms` | 一个慢 Session 对其他 Session 输出增加的延迟 | p95 增量 < 50 ms |
| `core_ping_latency_ms` | daemon 空闲时本地 `core.ping` 往返 | p95 < 50 ms |

性能测试必须记录机器、Python、SQLite journal 模式、可选 PostgreSQL 状态、并发数和数据规模。
单次结果不能作为验收结论。

## 3. 输出链路要求

### 3.1 Token 输出

- Token 通知不得执行数据库写入。
- Token 通知不得执行文件写入或 Telemetry flush。
- Token 通知必须在 asyncio Socket 所属事件循环中发送。
- 慢客户端必须受到发送超时或有界背压限制，不能永久占用 Agent worker。
- 一个客户端断开不得取消已经开始的 Core Turn，也不得影响其他连接。

### 3.2 回答完成

用户可见回答完成和派生维护完成是不同事实：

```text
response completed != maintenance completed
```

目标架构必须使派生维护不成为 CLI 恢复输入的条件。完整消息与最小 Session 状态仍是返回
成功前的耐久性屏障，但不得显示保存提示。

## 4. 数据库与后台任务隔离

### 4.1 普通会话提交

- 消息归档和 Session 状态更新应由单个事务完成。
- 最小提交必须短小，只包含不可重建的业务事实。
- 摘要、记忆和 checkpoint 清理必须通过持久化维护任务执行。
- 维护任务必须有状态、租约、有限重试和启动恢复策略。
- 同一 Session 的最小提交必须保持顺序；不同 Session 允许并行。
- 最小提交失败不能被误报为成功持久化。

### 4.2 Telemetry

- Telemetry 是 best-effort 观测，不得决定 Agent 结果。
- 任何包含 IO 的 Sink 默认必须异步或缓冲执行。
- 队列满时优先丢弃 Telemetry，而不是阻塞用户输出。
- Telemetry 写入不得长期占满业务数据库连接。
- 当共享连接池产生可测量的业务延迟时，应拆分业务池与 Telemetry 池。

### 4.3 上下文压缩与记忆

- 普通长期记忆提取必须后台执行。
- 用户明确要求记忆时返回 `pending`，不得虚假确认成功。
- 上下文压缩不得阻塞已经开始的 token 流。
- 后台压缩更新 Session 时必须进行版本检查，防止覆盖更新后的状态。

## 5. 可靠性要求

| 场景 | 要求 |
|---|---|
| PostgreSQL 暂时变慢 | 当前回答继续输出；后台任务有限等待和重试 |
| Telemetry 数据库不可用 | Agent 正常工作；记录可诊断错误 |
| Telemetry 队列满 | 丢弃观测事件并记录计数；不阻塞输出 |
| 客户端断开 | 停止通知；Core Turn 继续完成 |
| Core 正常关闭 | 停止接收新请求，并在超时内 drain 后台任务 |
| Core 强制终止 | 已返回成功的最小提交不得丢失；允许丢失 best-effort Telemetry，持久化维护任务下次启动恢复 |
| 同一 Session 快速连续请求 | 不覆盖、不乱序、不读取未提交旧状态 |

## 6. 可观测性要求

为了判断非功能需求是否满足，至少需要记录：

- 首 token 时间。
- token 转发耗时和 Socket drain 耗时。
- 最后 token 到前端释放的耗时。
- 最小业务提交耗时和失败数。
- 上下文压缩耗时。
- 长期记忆提取耗时。
- Agent worker、维护任务、记忆和 Telemetry 队列长度。
- 数据库连接获取耗时。
- Telemetry 丢弃计数。

性能指标不得包含完整 prompt、完整工具输出、密钥或 `.env` 内容。

## 7. 当前实现状态与缺口

| 要求 | 当前状态 |
|---|---|
| Agent Turn 不阻塞 asyncio 网络主循环 | 已实现：同步 Turn 在专用线程池执行 |
| PostgreSQL Telemetry 后台批量写入 | 已实现：`BufferedEventSink` |
| Telemetry 队列满不阻塞生产者 | 已实现：`put_nowait()` 并丢弃 |
| EventBus 关闭不阻塞 asyncio 主循环 | 已实现：Core 通过工作线程关闭 |
| 派生维护不延迟 CLI 恢复输入 | 已实现：持久化 `maintenance_jobs` |
| 消息、Session、Execution 与维护任务单事务提交 | 已实现：`CompletedTurnCommitter` |
| 上下文压缩后台执行并带版本检查 | 已实现：摘要任务与 `summary_through_turn` CAS |
| 慢客户端发送超时 | **未实现** |
| IO 型 Console/JSONL Sink 默认缓冲 | **未实现** |
| 业务数据库池与 Telemetry 池资源隔离 | **未实现** |
| 完整延迟指标和回归门禁 | **未实现** |

上述未实现项属于明确工程债务。后续优化不得以降低 Session 一致性或错误可观测性为代价。
