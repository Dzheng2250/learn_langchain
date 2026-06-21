# 面向接口的 Core 设计

> 文档状态：Current
>
> 权威范围：Core 内部依赖倒置、端口、适配器、IoC 组合方式。
>
> 维护触发：`src/core/ports/`、`src/core/adapters/`、`CoreApp`、`CoreContainer` 或状态提交边界变化。

本文说明项目如何从“业务代码直接知道 SQLite 细节”逐步转向“业务代码只依赖领域接口”。

这里的接口不是外部 RPC 接口，而是 Python 内部的 `Protocol` 或小型抽象。它描述某个模块需要什么能力，但不规定能力由 SQLite、文件、PostgreSQL 还是内存实现。

## 本文负责

本文只解释 Core 内部的依赖方向和可替换边界：

- `ports/` 定义哪些内部能力接口。
- `adapters/` 如何实现这些接口。
- `CoreApp` / `CoreContainer` 如何通过 IoC 和 DI 装配具体实现。
- 新增存储、Provider、队列或观测实现时应如何接入。

## 本文不负责

本文不展开具体业务流程或数据库 Schema：

- Turn 执行流程属于 [Agent 执行架构](/docs/architecture/agent-execution-architecture.md)。
- 数据库表、事务、Outbox 和 checkpoint 一致性属于
  [本地数据库设计与一致性机制](/docs/architecture/database-state-and-consistency.md)。
- 外部协议和前端接入属于 `/docs/api/`。
- 为什么选择某个方案属于 `/docs/decisions/`。

## 1. 优化目标

Agent 核心代码应该关心业务动作：

```text
加载会话上下文
追加本轮消息
更新 Session 近期上下文
完成 Execution / Slice
写入后台维护任务
```

它不应该关心底层细节：

```text
消息存 SQLite 表还是 JSONL 文件
字段叫 raw 还是 payload
事务对象是不是 sqlite3.Connection
后台任务表用什么 SQL 写入
```

如果这些细节散落在 Agent 服务、finalization 服务或 handler 中，后续新增文件历史、测试内存实现或 PostgreSQL 投影时，就会不断修改业务层代码，维护成本会快速上升。

## 2. 当前依赖方向

目标依赖方向是：

```text
Application Service
  -> src/core/ports      # 只描述业务能力
  -> src/core/adapters   # 具体实现能力
```

当前已经落地的核心端口包括：

| 端口 | 职责 |
|---|---|
| `ConversationHistoryStore` | 追加和读取会话消息历史 |
| `SessionStore` | 读取和更新 Session 摘要、近期消息、`turn_index` |
| `SessionLifecycleStore` | 解析 Session 身份并执行归档、删除、历史重建等生命周期持久化 |
| `ExecutionStore` | 在成功 Turn 的 Unit of Work 中完成最终 Slice / Execution |
| `ExecutionLifecycleStore` | 创建、查询、恢复和暂停前台 Execution |
| `ExecutionPauseStore` | 只持久化暂停状态，供预算暂停等窄场景使用 |
| `ExecutionSliceStore` | 创建和结束单个有界 Slice |
| `ExecutionFailureStore` | 为错误处理组合暂停与 Slice 收尾能力 |
| `MaintenanceQueue` | 将后台维护任务写入可靠队列 |
| `StateUnitOfWork` | 把一次成功 Turn 的多项状态修改放进同一个原子提交 |
| `StateUnitOfWorkFactory` | 为具体后端创建 Unit of Work |
| `StateInitializer` | 在 daemon 启动时初始化权威状态 schema，不暴露完整 Store |
| `ModelProvider / ModelConfiguration` | 定义在 `llm/contracts.py` 的供应商无关模型能力；具体 `ChatOpenAI` 构造留在 adapter 实现 |

旧的 `src/core/state/contracts.py::StateStore` 也已经收缩为前台 Turn 真正需要的能力：加载 Session、召回长期记忆和构造记忆提示消息。它不再声明 `append_messages_in_transaction()` 或 `save_fast_session_in_transaction()` 这类带数据库事务细节的方法。

当前生产实现仍然是 SQLite：

```text
src/core/adapters/sqlite/
  conversation_history.py
  session_store.py
  session_lifecycle.py
  memory_store.py
  memory_write_store.py
  projection_outbox.py
  summary_store.py
  unit_of_work.py
```

`LocalStateStore` 仍保留为兼容 facade，但它的职责正在缩小。新增能力应优先进入对应 adapter，而不是继续塞回 `LocalStateStore`。

## 3. 已完成的拆分

当前已经完成的低风险和中风险拆分如下：

| 能力 | 当前实现 | 说明 |
|---|---|---|
| 按 Turn 读取消息 | `SQLiteConversationHistoryStore.load_turn()` | 保持消息顺序和 message id 顺序 |
| 重建近期上下文 | `SQLiteConversationHistoryStore.rebuild_recent()` | 从完整归档恢复 `recent_messages` |
| 追加消息 | `SQLiteConversationHistoryStore.append_messages()` | 负责 `messages.raw`、role/content 提取和 active branch head 推进 |
| 读取 Session 上下文 | `SQLiteSessionStore.load_context()` | 返回 `AgentContextState` 和 `turn_index` |
| 保存完整 Session 上下文 | `SQLiteSessionStore.save_context()` | 更新 summary、recent messages、context tokens 和 turn index |
| 保存 fast context | `SQLiteSessionStore.save_fast_context()` | 响应关键路径使用，不覆盖后台摘要 |
| 前台长期记忆召回 | `SQLiteMemoryRetrievalStore.retrieve_for_turn()` | 处理 bootstrap/relevant 去重和长度限制 |
| 后台长期记忆写入 | `SQLiteMemoryWriteStore.extract_and_save()` | 负责提取、去重、来源关系、outbox 和事件 |
| PostgreSQL 投影 outbox | `SQLiteProjectionOutboxStore.enqueue()` | 负责可选投影事件写入 |
| 摘要读取与 CAS 写回 | `SQLiteSummaryStore` | 防止旧摘要覆盖新摘要 |
| 成功 Turn 原子提交 | `SQLiteStateUnitOfWorkFactory` | 组合 history/session/execution/maintenance 端口 |
| Agent facade | `AgentTurnService` | 仅委托异步执行、同步事件流和服务生命周期，不创建任何 provider、store、repository 或 worker |
| Agent 请求路由 | `AgentRequestStreamService` | 只依赖诊断、Execution、Runtime Graph、锁和执行流的行为 Protocol，不导入具体服务类 |
| Runtime Graph 解析 | `RuntimeGraphResolver` | 通过 `WorkspaceRuntimeProvider` 获取 runtime，不依赖具体 Registry 缓存实现 |
| 前台上下文读取 | `ConversationContextLoader` | 通过 `SessionStore` 和 `MemoryRetrievalStore` 读取上下文，不创建兼容 State facade |
| Agent 循环编排 | `TurnExecutionLoop` | 只执行 Slice 循环；不创建 Store，observer、错误/暂停处理器和 `LoopConfig` 均由组合根显式注入 |
| Agent 服务生命周期 | `AgentServiceLifecycle` | 初始化 state/checkpoint/recovery/maintenance，关闭 worker 和后台资源 |
| Session 并发锁 | `SessionLockRegistry` | 独立保存 Session UUID 到 reentrant lock 的映射 |
| Session 状态查询 | `SessionStatusReader` | 通过 `SessionLifecycleStore` 与 `SessionStore` 聚合 context、pending execution 和 maintenance 状态 |
| Session 生命周期持久化 | `SQLiteSessionLifecycleStore` | 隔离 Workspace repository、checkpoint thread 查询和历史重建的 SQLite 细节 |
| Session 生命周期编排 | `SessionLifecycleService` | 只协调归档、删除、重置和 pending execution，不再创建或关闭具体 Store |
| Agent 循环配置 | `LoopConfig` | 收敛 `TurnExecutionLoop` 的标量配置，避免构造器随配置项膨胀 |

这意味着响应关键路径中的消息追加和 Session fast context 更新已经不再由 `LocalStateStore` 自己执行 SQL，而是委托给 SQLite adapter。

## 4. Unit of Work 是什么

Unit of Work 可以理解为“一次业务操作里的所有状态修改”。

一次成功 Turn 至少要同时完成：

```text
写入完整消息
更新 Session 近期上下文
完成 Execution / Slice
写入后台维护任务
```

这些操作必须一起成功或一起回滚。否则会出现半完成状态，例如“AI 回答已经展示，但历史没有保存”，或者“消息保存了，但摘要任务丢了”。

现在 `CompletedTurnCommitter` 不直接持有 SQLite 连接，而是依赖：

```text
StateUnitOfWorkFactory
```

提交流程描述的是业务事实：

```text
with unit_of_work_factory.begin() as uow:
    uow.history.append_turn(completed)
    uow.sessions.save_fast_context(completed)
    uow.executions.finish_completed_turn(completed)
    uow.maintenance.enqueue(job)
    uow.commit()
```

这里没有出现表名、SQL 或 `sqlite3.Connection`。这是接口化的关键收益。
`begin()` 也不再接收 `LocalStateStore` 作为写入委托，避免 Unit of Work 反向依赖兼容 facade。

## 5. IoC 与 CoreApp

IoC 是 Inversion of Control，通常翻译为“控制反转”。在本项目中，它的意思是：

```text
业务服务不要自己创建数据库、Provider、Scheduler 或 Runtime
CoreApp / CoreContainer 在组合根统一创建并注入这些依赖
```

当前使用 `dependency-injector` 作为 Core daemon 的依赖注入容器。选择它的原因：

- 它是成熟的 Python DI 容器，支持 `Singleton`、`Factory`、`Object` 和测试 override。
- 它不要求项目变成 HTTP/ASGI 应用，适合当前 TCP + NDJSON + JSON-RPC daemon。
- 它能把对象创建集中到 `CoreContainer`，避免 `CoreApp.__init__` 继续膨胀。

没有选择 FastAPI `Depends` 的原因：

- Core 不是 HTTP 服务。
- FastAPI DI 与 HTTP request lifecycle、route handler 和 ASGI app 强绑定。
- 为了 DI 引入 HTTP 框架，会让 transport 决策反向污染 Core 内部设计。

当前组合关系是：

```text
CoreApp
  -> CoreContainer
      -> SQLite adapters / repositories
      -> ModelProvider / ContextManager
      -> MaintenanceScheduler
      -> TurnFinalizer / AgentTurnService / TurnExecutionLoop / TurnRunObserver
      -> Router / Handlers / Transport
```

业务模块不应直接创建 trace/event bus、数据库连接、模型 provider 或 runtime registry。这些基础设施应由 `CoreApp` 和 `CoreContainer` 注入。

Agent 执行链路也遵循同一原则：`AgentTurnService` 只保留面向 RPC 的 facade；构造器只接收 `AgentAsyncTurnRunner`、
`AgentRequestStreamService` 和 `AgentServiceLifecycle`。具体 provider、store、Execution、
maintenance、runtime graph 和 worker 的依赖组装全部由 `CoreContainer` 完成。
`create_parent_graph()`、Workspace runtime、子 Agent、文件摘要、上下文摘要和记忆提取
都要求显式传入 `ModelProvider`，并只从 `src/core/llm/contracts.py` 导入协议。
`ChatOpenAI` 与 `OpenAICompatibleProvider` 留在 `llm/provider.py`，具体 provider 只允许由
`CoreContainer` 创建，业务模块不再导入实现模块或提供隐式 fallback，
`AgentRequestStreamService` 通过 `AgentSessionStore` 获取 Workspace/Session 身份，并通过
`DiagnosticTurnStreamer`、`ExecutionLifecycleController`、`RuntimeGraphProvider` 和
`LockedTurnStreamer` 协调请求，不再导入对应具体实现。`RuntimeGraphResolver` 自身也只依赖
`WorkspaceRuntimeProvider`，Workspace runtime 的缓存与工厂实现仍留在组合根一侧。无模型配置的 `DiagnosticTurnService`
也改为依赖 `SessionStore`，不再创建兼容 Store。`AgentServiceLifecycle` 通过 `StateInitializer` 初始化权威状态 schema，不再创建兼容 Store；它同时负责 durable resource 的启动/关闭，
`ConversationContextLoader` 通过 `SessionStore` 和 `MemoryRetrievalStore` 加载前台输入，
`TurnExecutionLoop` 不再创建或关闭 `LocalStateStore`。`TurnFinalizer` 也不再接收未使用的 Store，
成功提交只经由 `CompletedTurnCommitter -> StateUnitOfWorkFactory` 完成。
`TurnExecutionLoop` 负责控制 Slice 循环和继续/停止判断；它不再自行创建 observer、错误处理器、
暂停处理器或配置，这些协作者统一由 `CoreContainer` 装配。`SliceExecutionService` 负责单次 LangGraph 执行，
`TurnLoopErrorHandler` 负责错误分支的
Execution 状态落库，`TurnLoopPauseHandler` 负责预算暂停的摘要、状态和事件构造。
这些 Agent Core 组件按需依赖 `ExecutionFailureStore`、`ExecutionPauseStore`、
`ExecutionLifecycleStore` 或 `ExecutionSliceStore`，不再暴露或命名具体 `ExecutionRepository`。
`TurnRunObserver` 负责把运行状态转换成 Telemetry/Trace，`AgentAsyncTurnRunner` 负责把阻塞式
Agent 执行提交到有界 worker，`AgentSyncTurnRunner` 负责在 worker 线程中消费同步事件流并聚合最终 RPC result。
执行逻辑不直接依赖具体 sink，观测逻辑也不决定业务是否继续。

`container_factories.py` 是组合根的辅助模块，只保存无状态构造函数，例如 transport、EventBus
和 maintenance handler map。它不是业务端口，也不应该被 Agent、Session、Memory 或 Tool 模块导入。

## 6. 当前仍保留的兼容层

`LocalStateStore` 目前仍作为兼容 facade 存在，原因是项目中仍有旧调用路径依赖它的方法名。

它现在主要负责：

- 把旧方法委托给新的 SQLite adapter。
- 保留旧的 foreground / maintenance store 方法名，方便现有服务逐步迁移到更小端口。
- 提供旧测试和维护任务仍在使用的统一入口。

它不应该继续承载新的业务能力。后续新增或迁移状态能力时，应优先新增端口和 adapter。

投影 outbox 的写入也应统一经过 `SQLiteProjectionOutboxStore`。其它 adapter 不应重复编写
`INSERT INTO projection_outbox` SQL，否则会让 outbox payload、开关语义和事务要求分散到多个地方。

## 7. 扩展与技术债务

新增内部 Port、Adapter、存储后端和 Contract Test 的操作步骤见
[内部端口与 Adapter 扩展指南](/docs/development/internal-adapter-extension.md)。

尚未完成的接口化工作统一登记在
[接口化重构技术债务](/docs/development/interface-refactor-backlog.md)。

本篇不再混合开发教程和未来计划。
## 8. 当前设计边界

当前实现仍以 SQLite 作为权威状态库。PostgreSQL 是可选投影，不是核心业务事实来源。Trace 是诊断时间线，不参与恢复。Telemetry 是观测事件，不作为任务状态。

接口化的目的不是隐藏所有复杂性，而是把复杂性限制在 adapter 和组合根中，让 Agent、finalization、maintenance 等业务模块只依赖清晰的小接口。
