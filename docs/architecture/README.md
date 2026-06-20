# 架构文档索引

> 文档状态：Current
> 权威范围：架构文档入口、模块分组和阅读顺序
> 维护触发：新增、移动、废弃或重命名架构文档

本文是 `docs/architecture/` 的目录级入口。它不替代任何专项文档，只负责告诉读者应该先读哪篇，
以及每类架构文档的职责边界。

## 本文负责

- 按模块组织架构文档。
- 说明每组文档负责回答的问题。
- 给出推荐阅读顺序。
- 防止 Core、Agent、State、Observability、Interface 文档互相复制设计细节。

## 本文不负责

- 不定义外部 RPC、事件字段或 CLI 命令；这些属于 `/docs/api/`。
- 不定义产品需求或路线图；这些属于 `/docs/product/`。
- 不记录设计取舍历史；这些属于 `/docs/decisions/`。
- 不记录部署、备份或回滚步骤；这些属于 `/docs/operations/`。

## 推荐阅读顺序

| 读者目标 | 阅读顺序 |
|---|---|
| 快速理解整体结构 | [系统架构总览](/docs/architecture/system-overview.md) -> [CoreApp 与 Transport 架构](/docs/architecture/core-architecture.md) |
| 理解一次 Agent 如何执行 | [Agent 执行架构](/docs/architecture/agent-execution-architecture.md) -> [函数级调用链](/docs/architecture/agent-execution-call-chain.md) -> [可恢复执行](/docs/architecture/resumable-execution.md) |
| 理解为什么响应后不应卡住 | [最终响应与后台维护](/docs/architecture/response-finalization-and-checkpoint-consistency.md) -> [本地数据库与一致性](/docs/architecture/database-state-and-consistency.md) |
| 理解会话、记忆和上下文 | [本地优先 Session 状态](/docs/architecture/local-first-session-state.md) -> [记忆管理](/docs/architecture/memory-management.md) |
| 理解接口化重构 | [面向接口的 Core 设计](/docs/architecture/interface-driven-core.md) |
| 理解排障和观测 | [Telemetry Event](/docs/architecture/event-system.md) -> [系统 Trace](/docs/architecture/system-tracing.md) |

## 模块分组

### 1. System / Core

这一组解释进程、组合根、Transport 和安全边界。

- [系统架构总览](/docs/architecture/system-overview.md)
- [CoreApp 与 Transport 架构](/docs/architecture/core-architecture.md)
- [CLI 架构](/docs/architecture/cli-architecture.md)
- [TUI 架构](/docs/architecture/tui-architecture.md)
  - 用户命令和能力边界见 [TUI 使用与命令参考](/docs/api/tui-reference.md)。
- [安全模型](/docs/architecture/security-model.md)

边界要求：

- 可以说明 Core 如何装配和关闭组件。
- 不应展开 Agent 内部循环、数据库表结构或 RPC 字段清单。

### 2. Agent

这一组解释 Agent 如何执行、暂停、恢复、调用模型和工具。

- [Agent 执行架构](/docs/architecture/agent-execution-architecture.md)
- [Agent 执行函数级调用链](/docs/architecture/agent-execution-call-chain.md)
- [可恢复执行与预算控制](/docs/architecture/resumable-execution.md)
- [私有任务规划](/docs/architecture/private-task-planning.md)
- [Provider 错误处理](/docs/architecture/provider-error-handling.md)

边界要求：

- 可以说明 `AgentTurnService`、`TurnExecutionLoop`、`SliceExecutionService`、工具调用和错误分支。
- 数据库提交只应写到 `TurnFinalizer` 这个抽象层，不应复制 `state.db` 表结构、Outbox 或 CAS 规则。

### 3. State

这一组解释本地状态、消息历史、上下文、记忆、事务和 checkpoint 一致性。

- [本地优先 Session 状态](/docs/architecture/local-first-session-state.md)
- [本地数据库设计与一致性机制](/docs/architecture/database-state-and-consistency.md)
  - 表关系和约束见 [本地状态数据库 Schema 参考](/docs/reference/local-state-schema.md)。
- [最终响应、后台维护与 Checkpoint 一致性](/docs/architecture/response-finalization-and-checkpoint-consistency.md)
- [记忆管理](/docs/architecture/memory-management.md)

边界要求：

- 可以说明 `state.db`、`checkpoints.db`、事务、Outbox、CAS、Saga、后台维护和消息链。
- 不应解释 CLI/TUI 如何渲染，也不应重新定义 RPC 参数。

### 4. Interfaces / IoC

这一组解释内部接口、适配器和依赖注入。

- [面向接口的 Core 设计](/docs/architecture/interface-driven-core.md)

边界要求：

- 可以说明 `ports/`、`adapters/`、`CoreContainer`、Unit of Work 和可替换实现。
- 不应把某个 adapter 的 SQL 细节写成 Core 服务职责。

### 5. Observability

这一组解释运行观测与排障。

- [Telemetry Event 系统](/docs/architecture/event-system.md)
- [系统 Trace 时间线](/docs/architecture/system-tracing.md)

边界要求：

- 可以说明 EventBus、Recorder、Sink、TraceRecord 和 trace 文件。
- 不应把 Telemetry 或 Trace 写成业务事实来源；业务事实以 State 文档为准。

## 写作约束

新增架构文档时：

1. 先判断它属于上面的哪一组。
2. 在文档头部声明 `本文负责` 和 `本文不负责`。
3. 如果内容跨模块，只保留摘要并链接到权威文档。
4. 不把 Decision、历史 Review、部署命令或 API 字段清单混入架构正文。
5. 更新本文、[文档中心](/docs/README.md) 和
   [文档登记表](/docs/governance/document-register.md)。
