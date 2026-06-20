# 面向接口的 Core 设计

> 文档状态：Current
>
> 权威范围：Core 内部依赖倒置、端口、适配器、IoC 组合方式。
>
> 维护触发：`src/core/ports/`、`src/core/adapters/`、`CoreApp`、`CoreContainer` 或状态提交边界变化。

本文说明项目如何从“业务代码直接知道 SQLite 细节”逐步转向“业务代码只依赖领域接口”。

这里的接口不是外部 RPC 接口，而是 Python 内部的 `Protocol` 或小型抽象。它描述某个模块需要什么能力，但不规定能力由 SQLite、文件、PostgreSQL 还是内存实现。

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
| `ExecutionStore` | 完成 Slice / Execution 等执行生命周期变更 |
| `MaintenanceQueue` | 将后台维护任务写入可靠队列 |
| `StateUnitOfWork` | 把一次成功 Turn 的多项状态修改放进同一个原子提交 |
| `StateUnitOfWorkFactory` | 为具体后端创建 Unit of Work |

旧的 `src/core/state/contracts.py::StateStore` 也已经收缩为前台 Turn 真正需要的能力：加载 Session、召回长期记忆和构造记忆提示消息。它不再声明 `append_messages_in_transaction()` 或 `save_fast_session_in_transaction()` 这类带数据库事务细节的方法。

当前生产实现仍然是 SQLite：

```text
src/core/adapters/sqlite/
  conversation_history.py
  session_store.py
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
| Agent 服务生命周期 | `AgentServiceLifecycle` | 初始化 state/checkpoint/recovery/maintenance，关闭 worker 和后台资源 |
| Session 并发锁 | `SessionLockRegistry` | 独立保存 Session UUID 到 reentrant lock 的映射 |
| Session 状态查询 | `SessionStatusReader` | 只读聚合 context、pending execution 和 maintenance 状态 |
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

Agent 执行链路也遵循同一原则：`AgentTurnService` 只保留面向 RPC 的 facade 和依赖组装，
`AgentServiceLifecycle` 负责 durable resource 的启动/关闭，`TurnExecutionLoop` 负责控制 Slice 循环和继续/停止判断，
`SliceExecutionService` 负责单次 LangGraph 执行，`TurnLoopErrorHandler` 负责错误分支的
Execution 状态落库，`TurnLoopPauseHandler` 负责预算暂停的摘要、状态和事件构造，
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

## 7. 后续还需要接口化的部分

后续优先级建议如下：

1. **Workspace / Session Lifecycle**
   Workspace 注册、Session 归档、硬删除、重置等能力应继续收敛到明确的 lifecycle service 和 store 端口中。
   当前 `SessionLifecycleService` 已经把状态查询拆给 `SessionStatusReader`，只读 status 不再混在归档、删除、重置等写路径里；
   RPC 返回字典由
   `src/core/session/responses.py` 构造，checkpoint cleanup 入队由
   `src/core/session/checkpoint_cleanup.py` 负责。后续如果继续拆，应优先把 Session 读写能力抽成更小的
   `SessionLifecycleStore`，而不是让 lifecycle service 直接依赖具体 workspace repository 方法。

2. **Task Store**
   私有任务规划已经拆出 `TaskPlanValidator` 和 `TaskQueryStore`，分别承载纯规则校验与任务查询装配。
   任务写入 SQL 已拆到 `TaskMutationStore`，Execution 身份校验已拆到 `TaskExecutionContextGuard`。
   `TaskRepository` 现在主要负责事务编排：校验输入、读取当前图、调用 mutation helper、再验证依赖状态。
   后续如果要支持文件或 PostgreSQL 任务后端，应继续抽出 `TaskStore` 端口，避免上层依赖具体 repository。

3. **Execution Store**
   `PendingExecution` 和 SQLite 行映射已经拆到 `src/core/state/execution_models.py`。
   `get_pending()` 和 `get_attached()` 等只读查询已经拆到 `ExecutionQueryStore`。
   checkpoint 清理、缺失和重启恢复对账已经拆到 `ExecutionCheckpointStore`。
   Slice 创建、完成和预算计数已经拆到 `ExecutionSliceStore`。
   complete/discard/terminate 这类“结束 Execution 并释放 Session”的写入已经拆到 `ExecutionReleaseStore`。
   `ExecutionRepository` 仍保留 begin/resume/pause 等入口，后续可继续按“创建与恢复”“暂停状态更新”拆成更小 adapter 或 query helper。

4. **Migration Orchestration**
   PostgreSQL 到本地状态的迁移入口仍是 `LocalStateMigration`，但源库计数检查已经拆到
   `LocalStateMigrationInspector`，源库清理已经拆到 `LocalStateSourcePruner`。后续如果继续拆，应优先拆 `_copy()` 中的 workspace/session、messages、memories、events 四段复制逻辑。
   旧 PostgreSQL workspace schema 迁移的 `WorkspaceMigration` 也不再包含 `pg_dump` / Docker 备份细节；
   备份逻辑已经拆到 `src/core/database/backup.py`，迁移编排只依赖 `create_database_backup()`。

5. **Memory Write / Read Contract Tests**
   本地 SQLite 记忆读取和写入已经拆出 adapter。兼容 PostgreSQL memory facade 也已经把格式化、检索辅助和写入事务拆到 `formatting.py`、`retrieval.py`、`writer.py`。
   后续还需要更完整的 contract tests，方便未来替换为向量检索或混合后端。

6. **File Conversation History 原型**
   文件历史后端可以作为实验实现，但不能直接替代权威状态库，除非它能满足同等的事务和恢复语义。

7. **Context Summary Execution**
   `AgentContextManager` 仍负责上下文窗口、摘要触发判断和状态更新，但 LLM 摘要调用已经拆到
   `ContextSummaryExecutor`。这让上下文状态算法不直接依赖 provider 创建、prompt 构造、token usage
   解析和 telemetry span。后续如果更换摘要模型或加入无模型摘要策略，应优先扩展 executor，而不是继续扩大 manager。

8. **Maintenance Queue**
   `MaintenanceRepository` 仍是后台维护任务的兼容门面，但只读状态查询已经拆到
   `MaintenanceInspectionStore`，SQLite row 到领域对象的转换也已经拆到 `src/core/maintenance/records.py`。
   后续如果要替换维护队列后端，应继续把 enqueue、claim、succeed/fail/requeue 拆成明确的 `MaintenanceQueue`
   和 `MaintenanceLeaseStore` 端口。

## 8. 如何新增存储后端

以后如果要把会话历史从 SQLite 扩展到 JSONL 文件，不能在 `AgentTurnService` 或 `CompletedTurnCommitter` 里写文件逻辑。

正确路径是：

```text
新增 Adapter
  -> 实现 ports 中的 Protocol
  -> 在 CoreContainer / factory 中按配置选择 Adapter
  -> 复用同一组 contract tests
```

以 `FileConversationHistoryStore` 为例：

1. 在 `src/core/adapters/file/` 中实现 `ConversationHistoryStore`。
2. 明确文件布局，例如按 Workspace、Session 和日期拆分 JSONL。
3. 保证 `append_turn()` 至少不会产生半行 JSON。
4. 实现 `load_turn()` 和 `rebuild_recent()`，返回与 SQLite 版本等价的 LangChain message。
5. 不在业务服务里判断“当前是文件还是 SQLite”。
6. 用同一份 contract test 跑 SQLite 和 File 两个实现。

如果新后端无法提供原子事务，就不能直接实现完整 `StateUnitOfWork`，只能先作为历史副本、导出后端或调试后端。

## 9. 如何新增端口

新增端口前必须先确认它表达的是稳定业务能力，而不是某个数据库表的 CRUD。

推荐流程：

```text
业务场景
  -> 命名能力
  -> 定义 Protocol
  -> 写 Fake 实现测试业务服务
  -> 写 SQLite Adapter
  -> 在 CoreContainer 注入
```

端口应保持小而专。不要把前台召回、后台写入、运维查询全部塞进同一个接口。

例如长期记忆可拆成：

```text
MemoryRetrievalStore     # 前台 Turn 只需要召回
MemoryWriteStore         # 后台维护才需要提取和保存
MemoryInspectionStore    # 调试或运维才需要查询失败状态
```

## 10. 测试要求

接口化重构必须配套测试，否则只是移动代码。

最低要求：

- Adapter 单元测试：验证 SQLite 实现保持旧行为。
- Contract Tests：同一组行为测试可套到 SQLite、InMemory、File 等实现。
- 边界测试：application service 层不得 import `sqlite3` 或 `src.core.adapters.sqlite`。
- 回归测试：finalization、agent service、local state、documentation tests 必须通过。

新增 adapter 时，测试重点应覆盖：

```text
消息顺序
message_id 稳定性
raw 数据不丢失
事务回滚
摘要不被 fast context 覆盖
后台任务与业务状态同事务入队
```

## 11. 当前设计边界

当前实现仍以 SQLite 作为权威状态库。PostgreSQL 是可选投影，不是核心业务事实来源。Trace 是诊断时间线，不参与恢复。Telemetry 是观测事件，不作为任务状态。

接口化的目的不是隐藏所有复杂性，而是把复杂性限制在 adapter 和组合根中，让 Agent、finalization、maintenance 等业务模块只依赖清晰的小接口。
