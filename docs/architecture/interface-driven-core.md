# 面向接口的 Core 设计

> 文档状态：Current
> 权威范围：Core 内部依赖倒置、端口、适配器和 IoC 组合方式
> 维护触发：`src/core/ports/`、`src/core/adapters/`、`CoreApp` 或状态提交边界变化

本文说明项目如何从“业务代码直接知道 SQLite 细节”逐步转向“业务代码只依赖领域接口”。
这里的接口不是外部 RPC，而是 Python 代码内部的 `Protocol`：它定义某个模块需要什么能力，
但不规定能力由数据库、文件还是内存实现。

## 1. 为什么要做这层抽象

Agent 的核心代码关心的是业务问题：

```text
加载会话历史
追加本轮消息
更新 Session 上下文
完成 Execution
写入后台维护任务
```

它不应该关心：

```text
消息存在 SQLite 表还是 JSONL 文件
字段叫 raw 还是 payload
事务对象是不是 sqlite3.Connection
后台任务表的 SQL 怎么写
```

如果这些细节泄漏到 Agent 服务里，后续新增文件历史、PostgreSQL 投影或测试用内存实现时，
就必须改动大量业务代码。这会让系统越来越难维护。

## 2. 当前边界

核心依赖方向：

```text
Application Service
  -> src/core/ports          # 只描述能力
  -> src/core/adapters       # 具体实现能力
```

当前已经落地的端口：

| 端口 | 职责 |
|---|---|
| `ConversationHistoryStore` | 追加和读取会话消息历史 |
| `SessionStore` | 读取和更新 Session 摘要、近期消息和 turn_index |
| `ExecutionStore` | 完成 Slice / Execution 等执行生命周期变更 |
| `MaintenanceQueue` | 将后台维护任务写入可靠队列 |
| `StateUnitOfWork` | 把一次成功 Turn 的多项状态修改放入同一个原子提交 |
| `StateUnitOfWorkFactory` | 为具体后端创建 Unit of Work |

当前生产适配器仍是 SQLite：

```text
src/core/adapters/sqlite/
  conversation_history.py
  session_store.py
  memory_store.py
  unit_of_work.py
```

这个文件把现有 `LocalStateStore`、`ExecutionRepository` 和 `MaintenanceRepository`
包装成端口实现。这样做是有意的低风险迁移：先纠正依赖方向，再逐步拆分原来的大型状态门面。

当前已经完成第二轮低风险拆分：

- `SQLiteConversationHistoryStore` 接管历史读取能力，包括按 turn 读取消息和从完整归档重建近期上下文。
- `SQLiteSessionStore` 接管 Session 上下文读取，以及 Unit of Work 内的 fast context 更新委托。
- `SQLiteMemoryRetrievalStore` 接管前台 turn 的长期记忆召回和记忆提示消息构造。
- `SQLiteStateUnitOfWorkFactory` 继续负责一次成功 turn 的原子提交。

`LocalStateStore` 仍保留为兼容 facade。它现在把低风险读取能力委托给这些 adapter，但仍持有若干高风险写入能力。这样做是有意的：先缩小万能门面的职责，再逐步迁移响应关键路径。

## 3. Unit of Work 是什么

Unit of Work 可以理解为“一次业务操作的一组修改”。一次成功 Turn 至少要同时完成：

```text
写入完整消息
更新 Session 最近上下文
完成 Execution 和 Slice
写入后台维护任务
```

这些操作必须一起成功或一起回滚。否则会出现“AI 回答已经展示，但历史没有保存”或者
“消息保存了，但后台摘要任务丢了”的半完成状态。

现在 `CompletedTurnCommitter` 不再直接持有 `LocalStateDatabase`，而是依赖：

```text
StateUnitOfWorkFactory
```

提交流程变成：

```text
with unit_of_work_factory.begin(store) as uow:
    uow.history.append_turn(completed)
    uow.sessions.save_fast_context(completed)
    uow.executions.finish_completed_turn(completed)
    uow.maintenance.enqueue(job)
    uow.commit()
```

这段代码描述的是业务事实，不是数据库实现。

## 4. IoC ? CoreApp

IoC ? Inversion of Control?????????????????????

```text
?????? new ????Provider?Scheduler ? Runtime?
CoreApp ??????????????????????
```

`CoreApp` ???????????????????? `dependency-injector` ? `CoreContainer` ???

?? `dependency-injector` ????

- ????? Python DI ????? `Singleton`?`Factory`?`Object` ? provider override?
- ????????? `CoreApp.__init__` ?????????????????
- ????????? HTTP/ASGI?????? FastAPI ????????
- ?????? provider override ?? Agent service?Transport??????????

???? FastAPI `Depends` ?????? Core ??? TCP + NDJSON + JSON-RPC daemon??? HTTP ??????? FastAPI DI ???????????????????

??????

```text
CoreApp
  -> CoreContainer
      -> SQLite adapters / repositories
      -> ModelProvider / ContextManager
      -> MaintenanceScheduler
      -> TurnFinalizer / AgentTurnService
      -> Router / Handlers / Transport
```

????????????????trace/event bus ??????????? `CoreApp` ???????????????????????????

????????? import?

```text
sqlite3
src.core.adapters.sqlite
dependency_injector
```

????? `tests/contracts/test_interface_boundaries.py` ???


## 5. 为什么首版不直接拆完 LocalStateStore

`LocalStateStore` 目前仍包含会话、消息、记忆和摘要等多类逻辑。直接一次性拆开风险较高：

- 容易破坏响应关键路径的事务语义。
- 容易改变消息序列化格式，影响历史恢复。
- 容易让记忆提取、摘要维护和 session 状态出现细微不一致。

因此当前采用两步迁移：

1. 先用端口和 SQLite Unit of Work 包住现有实现，确保上层不再依赖裸 SQLite 事务。
2. 再把 `LocalStateStore` 内部逐步拆成 `SQLiteConversationHistoryStore`、
   `SQLiteSessionStore`、`SQLiteMemoryStore` 等独立适配器。

这样可以让每一步都有测试保护。

截至当前实现，以下能力已经拆出：

| 能力 | 当前实现 | 风险等级 |
|---|---|---|
| 按 turn 读取消息 | `SQLiteConversationHistoryStore.load_turn()` | 低 |
| 从归档重建近期上下文 | `SQLiteConversationHistoryStore.rebuild_recent()` | 低 |
| 读取 Session 上下文 | `SQLiteSessionStore.load_context()` | 低 |
| Unit of Work 内保存 fast context | `SQLiteSessionStore.save_fast_context()` | 中 |
| 前台长期记忆召回 | `SQLiteMemoryRetrievalStore.retrieve_for_turn()` | 低 |

以下能力仍留在 `LocalStateStore`，暂不拆出：

| 能力 | 暂缓原因 |
|---|---|
| `append_messages_in_transaction()` | 直接影响完整消息历史、分支头和 execution 关联 |
| `_append_messages()` | 负责 `messages.raw` 的 LangChain 消息序列化格式 |
| `save_fast_session_in_transaction()` | 属于响应前最小提交路径 |
| `extract_and_save_memories()` | 涉及 LLM 记忆提取、来源关系和去重 |
| `update_summary_cas()` | 涉及摘要 CAS，拆错会导致旧摘要覆盖新摘要 |

这些方法必须等 contract tests 更完整后再迁移。

## 6. 后端选择配置

配置中已经预留后端选择变量：

```text
LEARN_AGENT_CONVERSATION_HISTORY_BACKEND=sqlite
LEARN_AGENT_MEMORY_BACKEND=sqlite
LEARN_AGENT_TASK_BACKEND=sqlite
LEARN_AGENT_CHECKPOINT_BACKEND=sqlite
```

当前版本只支持生产级 `sqlite`。这些变量的意义是固定接口边界，而不是承诺其他后端已经可用。
未来如果实现 `FileConversationHistoryStore`，应接入同一组端口和契约测试。

## 7. 当前仍需继续重构的部分

当前改动解决了最关键的提交路径依赖问题，并拆出了第一块非执行职责：

- `LocalStateStore` 仍是兼容门面，后续应拆为更小的 SQLite adapter。
- `SessionLifecycleService` 已接管 `status / discard / reset / archive / hard delete`。
  RPC handler 通过独立接口调用它，不再要求 `AgentTurnService` 承担会话控制。
- `ConversationContextLoader` 已接管 session context、长期记忆召回和 graph 输入消息构建。
- `ExecutionLifecycleService` 已接管 Execution begin、resume、pending 判断和执行准备失败时的 pause。
- `TurnResultBuilder` 已接管 `run_turn/resume_execution` 的最终 result 聚合逻辑。
- `ProviderFailureService` 已接管非重试 provider error 的 Session 释放、checkpoint cleanup 入队和终止响应。
- `DiagnosticTurnService` now owns the no-LLM diagnostic turn response.
- `AgentTurnService` 仍然偏大，后续应继续拆出 Slice 循环执行器。
- 记忆、任务和 Workspace 也应逐步补充更细的端口契约。
- 文件历史后端暂未实现；当前只是让接口层为它留出位置。

这些限制是当前实现状态，不应在文档或代码中描述为已经完成。

## 8. 后续如何新增一个存储后端

以后如果要把会话历史从 SQLite 扩展到 JSONL 文件，不能直接在 `AgentTurnService`
或 `CompletedTurnCommitter` 里写文件逻辑。正确路径是：

```text
新增 Adapter
  -> 实现 ports 中的 Protocol
  -> 在 CoreApp 根据配置选择 Adapter
  -> 复用同一组契约测试
```

以 `FileConversationHistoryStore` 为例，最小实现步骤是：

1. 在 `src/core/adapters/file/` 下实现 `ConversationHistoryStore`。
2. 明确文件布局，例如按 Workspace、Session 和日期拆分 JSONL。
3. 保证 `append_turn()` 具有可恢复写入语义，至少不能产生半行 JSON。
4. 实现 `load_turn()` 和 `rebuild_recent()`，返回与 SQLite 版本等价的 LangChain message。
5. 不在业务服务中判断“当前是文件还是 SQLite”，只在 `CoreApp` 或专门的 factory 中选择实现。
6. 用同一份 contract test 跑 SQLite 与 File 两个实现。

如果新后端不能提供原子事务，就不能直接替换完整 `StateUnitOfWork`。这种情况下只能先作为
“历史副本”或“导出后端”，不能成为权威状态来源。

## 9. 后续如何新增一个端口

新增端口前必须先确认它表达的是稳定业务能力，而不是某个数据库表的 CRUD。

推荐流程：

```text
业务场景
  -> 命名能力
  -> 定义 Protocol
  -> 写 Fake 实现测试业务服务
  -> 写 SQLite Adapter
  -> 在 CoreApp 注入
```

例如要拆出长期记忆：

```python
class MemoryStore(Protocol):
    def retrieve_for_turn(...): ...
    def extract_and_save(...): ...
```

这个接口表达的是“为一轮对话召回记忆”和“从已提交消息提取记忆”，而不是
`SELECT memories` 或 `INSERT memory_sources`。

新增端口时不要一次性暴露太多方法。端口应该按调用方需要拆分：

- 前台 Turn 路径只需要召回能力。
- 后台维护路径才需要提取和保存能力。
- 运维或调试路径需要查询失败状态时，应使用单独的只读接口。

## 10. 后续如何继续拆 LocalStateStore

`LocalStateStore` 的拆分应按风险从低到高进行。

### 第一批：只读或低风险能力

- `load_turn_messages()` -> `SQLiteConversationHistoryStore.load_turn()`
- `rebuild_recent_messages_from_archive()` -> `SQLiteConversationHistoryStore.rebuild_recent()`
- `retrieve_bootstrap()` 和 `retrieve_relevant()` -> `SQLiteMemoryStore`

这些能力不在成功 Turn 的最小提交事务中，比较适合作为下一步拆分。

### 第二批：后台维护能力

- `load_summary_source()`
- `update_summary_cas()`
- `extract_and_save_memories()`

这些能力涉及 LLM 维护任务和 CAS，拆分时必须保留“旧摘要不能覆盖新摘要”的测试。

### 第三批：响应关键路径写入能力

- `append_messages_in_transaction()`
- `save_fast_session_in_transaction()`

这些是最敏感的能力。它们必须继续通过 `StateUnitOfWork` 统一提交，不能拆成多个独立事务。

每拆一批，都要满足：

```text
现有行为测试通过
新增 adapter contract test
业务服务没有新增具体实现 import
文档同步更新
```

## 11. 后续如何收缩 AgentTurnService

接口层解决的是“依赖谁”的问题，`AgentTurnService` 过大解决的是“谁负责什么”的问题。
这两个问题相关，但不是同一个问题。

推荐拆分顺序：

已完成：

1. `SessionLifecycleService`：接管 `status / discard / reset / archive / hard delete`
   等不需要模型执行的会话控制操作。
2. `ConversationContextLoader`：接管摘要、近期消息、长期记忆上下文加载，并返回
   `PreparedTurn`。
3. `ExecutionLifecycleService`：接管 Execution 创建、恢复、pending 判断和准备失败 pause。
4. `TurnResultBuilder`：接管流式事件到 RPC result 的聚合。
5. `ProviderFailureService`：接管非重试 provider error 的自动恢复与用户可见终止事件。

推荐继续拆分顺序：

1. Completed: `SliceExecutionService` owns one bounded LangGraph Slice execution.
2. Completed: `DiagnosticTurnService` owns the no-LLM diagnostic turn response.

拆分后 `AgentTurnService` 应只保留：

```text
创建本轮运行上下文
调用图执行器
调用 finalizer
产出流式事件
```

这个收缩必须分阶段做。每拆出一个 service，都要先用 Fake Store 或 Fake Port 写单元测试，
再接回 `CoreApp`。

## 12. DI 框架选择与扩展规则

当前项目使用 `dependency-injector` 作为 Core daemon 的依赖注入容器。它解决的是“对象由谁创建、具体实现在哪里选择”的问题：

```text
业务服务
  -> 只声明构造函数依赖
CoreContainer
  -> 选择 SQLite / ModelProvider / Scheduler / Transport 等具体实现
CoreApp
  -> 安装容器、启动和关闭生命周期
```

不选择 FastAPI `Depends` 的原因：

- 当前 Core 是本地 TCP + NDJSON + JSON-RPC daemon，不是 HTTP/ASGI 服务。
- FastAPI 的 DI 与 HTTP request lifecycle、route handler 和 ASGI app 强绑定；为了使用它而引入 HTTP 框架，会让 transport 决策反过来污染 Core 设计。
- 当前项目已经有 CLI、TUI 和自定义 transport，依赖注入应服务于 Core 内部对象图，而不是绑定到某一种前端协议。
- `dependency-injector` 支持 `Singleton`、`Factory`、`Object` 和测试 override，足够表达当前组合根需求。

新增依赖时遵守以下规则：

1. 业务服务不得直接 `new` 生产级基础设施，例如数据库连接、模型 provider、scheduler、runtime registry、trace writer。
2. 业务服务通过构造函数声明依赖，依赖类型优先是 `Protocol` 或小接口。
3. 具体实现只在 `CoreContainer` 或明确的 adapter factory 中选择。
4. 单元测试优先注入 fake / in-memory 实现，而不是启动真实数据库或 daemon。
5. 如果新增第三方框架，必须说明它解决的问题、为什么现有依赖不能解决、以及它是否会改变 Core 的协议边界。

本轮进一步拆分了 foreground Agent 执行链：

```text
AgentTurnService
  -> TurnWorkerExecutor          # bounded worker / async bridge
  -> AgentRequestStreamService   # workspace/session/graph request streaming
  -> TurnResultBuilder           # stream event -> final RPC result
```

`AgentRequestStreamService` 由 DI 容器注入，负责 `stream_turn` 与 `stream_resume` 的请求身份解析、Session 锁、配置检查、Execution 生命周期和 runtime graph 选择。`AgentTurnService` 保留兼容包装方法，但不再直接持有这段编排逻辑。
