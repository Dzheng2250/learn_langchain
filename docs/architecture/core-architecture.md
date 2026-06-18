# CoreApp 与 Transport 架构

> 文档状态：Current
> 权威范围：Core 组合根、生命周期、Transport 和内部依赖边界
> 维护触发：CoreApp、Transport、服务组装或关闭顺序变化

> Workspace 隔离、用户级 daemon、数据库迁移和最新依赖边界见
> [`/docs/decisions/workspace-isolation-and-migration.md`](/docs/decisions/workspace-isolation-and-migration.md)。
>
> 单次 Agent 执行、ModelProvider、RunContext、ToolRegistry 与事件通道见
> [`/docs/architecture/agent-execution-architecture.md`](/docs/architecture/agent-execution-architecture.md)。
>
> Telemetry Event、EventBus、Sink 和可靠性边界见
> [`/docs/architecture/event-system.md`](/docs/architecture/event-system.md)。
>
> IPC、Agent、LLM、Tool 和响应写回的跨层排障时间线见
> [`/docs/architecture/system-tracing.md`](/docs/architecture/system-tracing.md)。
>
> 用户可感知延迟、后台处理边界与验收方法见
> [`/docs/quality/non-functional-requirements.md`](/docs/quality/non-functional-requirements.md) 和
> [`/docs/quality/non-functional-testing.md`](/docs/quality/non-functional-testing.md)。
> 最终响应耐久性屏障、后台维护与 checkpoint 恢复见
> [`/docs/architecture/response-finalization-and-checkpoint-consistency.md`](/docs/architecture/response-finalization-and-checkpoint-consistency.md)。
> 模型服务商错误解析、可重试判断和 Execution 处置策略见
> [`/docs/architecture/provider-error-handling.md`](/docs/architecture/provider-error-handling.md)。
> Core 内部面向接口、Unit of Work 和 IoC 组合根边界见
> [`/docs/architecture/interface-driven-core.md`](/docs/architecture/interface-driven-core.md)。
> 数据库表、事务、Outbox、CAS、Saga 和恢复协调器的通俗说明见
> [`/docs/architecture/database-state-and-consistency.md`](/docs/architecture/database-state-and-consistency.md)。
> 端到端请求路径见其中的[完整数据流动示意图](/docs/architecture/agent-execution-architecture.md#完整数据流动示意图)，
> Agent 内部执行与双事件通道见[调用链与事件通道图](/docs/architecture/agent-execution-architecture.md#agent-调用链与事件通道图)。
>
> PR #3 review 整改与可靠性决策见
> [`/docs/history/pr-3-review-hardening.md`](/docs/history/pr-3-review-hardening.md)。
>
> PostgreSQL Compose 部署、用户级配置与环境覆盖规则见
> [`/docs/operations/deployment.md`](/docs/operations/deployment.md)。

## 重构目标

Core daemon 负责 Agent、工具、上下文、记忆和 RPC 服务。重构前的 `CoreRpcServer` 同时承担 TCP 连接、JSON-RPC 路由、业务 handler、Agent 服务创建和 daemon 生命周期，违反单一职责原则，也使 Transport 无法独立测试或替换。

本次重构将 Core 拆为：

```text
main
  -> CoreApp
      -> Handlers
      -> AgentTurnService
      -> SessionLifecycleService
      -> ConversationContextLoader
      -> ExecutionLifecycleService
      -> RpcRouter
      -> Transport
```

核心依赖方向：

```text
Transport -> RpcRouter
Handlers  -> RequestContext + Application Service
CoreApp   -> 组装所有组件并管理生命周期
Agent     -> 不依赖 RPC、Transport 或 CLI
```

## DI 组合方式

Core 使用 `dependency-injector` 描述进程级依赖图。`CoreApp` 仍然是生命周期入口，
但不再手写所有构造细节，而是从 `CoreContainer` 获取已经装配好的组件。

```text
CoreApp
  -> 创建/接收 CoreContainer
  -> 注入 CoreConfig、auth_token、shutdown_event、trace_recorder
  -> 从容器取得 AgentTurnService、SessionLifecycleService、ConversationContextLoader、ExecutionLifecycleService、Router、Handlers、Transport
  -> 按固定顺序 start / close
```

选择 `dependency-injector` 的原因：

- 它是成熟的 Python DI 容器，支持 `Singleton`、`Factory`、`Object` 和测试 override。
- 它不要求应用是 HTTP/ASGI 服务，适合当前 TCP/NDJSON/JSON-RPC daemon。
- 它能把对象图集中在组合根，业务服务仍然通过构造函数显式声明依赖。

约束：业务模块不能把容器当 service locator 使用。`AgentTurnService`、
`SessionLifecycleService`、`TurnFinalizer`、handlers 等应用层代码不得 import
`CoreContainer` 或 `dependency_injector`；这些边界由契约测试保护。


## 当前结构

```text
src/core/
  __main__.py
  main.py
  app.py

  config/
    models.py

  bus/
    context.py
    router.py

  handlers/
    core.py
    agent.py

  transport/
    framing.py
    socket_server.py

  agent/
    contracts.py
    coordinator.py
    graph.py
    service.py

  finalization/
    committer.py
    models.py
    service.py

  ports/
    state.py

  adapters/
    sqlite/
      unit_of_work.py

  maintenance/
    handlers.py
    models.py
    recovery.py
    repository.py
    scheduler.py

  state/
    contracts.py
    database.py
    executions.py
    schema.sql
    store.py
```

### `main.py`

进程入口只负责：

1. 解析 `serve` 命令参数。
2. 加载并验证 `CoreConfig`。
3. 读取或创建本地 token。
4. 创建 `CoreApp`。
5. 启动 asyncio 事件循环。

它不注册 RPC 方法，也不直接创建 Socket server。

### `CoreApp`

`CoreApp` 是组合根和生命周期管理器，负责：

- 创建 Router、handlers、Agent service 和 Transport。
- 注册 `core.ping`、`core.shutdown` 和 `agent.chat`。
- 按顺序初始化服务并启动 Transport。
- 写入和清理 PID 文件。
- 等待 shutdown 事件。
- 按反向顺序关闭 Transport、Agent 服务和事件 sink。

启动顺序：

```text
初始化并迁移 state.db
  -> 初始化 checkpoints.db
  -> ExecutionRecoveryCoordinator 跨库对账
  -> 启动 MaintenanceScheduler
  -> 准备 runtime 目录
  -> Transport start
  -> 写入 PID
  -> 等待 shutdown
```

关闭顺序：

```text
停止接受新连接
  -> 关闭客户端 stream，释放空闲连接
  -> 等待活跃 handler
  -> 等待 Transport 完全关闭
  -> 等待 Agent turn executor
  -> 停止 MaintenanceScheduler 认领新任务
  -> 关闭 checkpoint
  -> flush/关闭事件 sink
  -> 关闭数据库连接池
  -> 删除 PID
```

初始化中途失败时，`CoreApp` 会调用相同的关闭流程清理已经创建的资源。

### Handlers

Handlers 是协议与业务之间的适配层。

`CoreHandlers`：

- 实现 `core.ping`。
- 实现 `core.shutdown`。
- 不知道 TCP writer 或 Agent。

`AgentHandlers`：

- 将 `agent.chat` 参数转换为 `AgentTurnService.run_turn()` 调用。
- 将 Agent 事件转换为 `agent.event` notification。
- 只依赖 `AgentTurnRunner` 和抽象 `RequestContext`。
- 直接等待异步应用服务接口，不负责选择或管理执行线程池。

Handler 不负责连接读取、JSON 解码、鉴权或业务服务生命周期。

### Bus

Bus 表示经过验证的 RPC 调用边界。

`RpcRouter` 负责：

- 验证 JSON-RPC envelope。
- 验证 method 参数模型。
- 验证 token。
- 查找并调用 handler。
- 将 handler 结果包装为 JSON-RPC 响应。
- 隐藏内部异常细节并记录服务端日志。

`RequestContext` 是 handler 可使用的最小能力接口：

```text
request_id
send_notification()
request_close()
```

它不暴露 socket、writer 或 NDJSON 实现。

### Transport

Transport 只负责数据传输。

`SocketServer` 负责：

- 监听本机 TCP。
- 接受和关闭连接。
- 读取有大小限制的 NDJSON frame。
- 将消息交给 Router。
- 并发安全地写回响应和 notification。
- 停止接收新连接后关闭客户端 stream，并等待活跃 handler。

Transport 不允许依赖：

```text
AgentTurnService
Memory
Tools
Handlers
daemon PID/token 管理
```

## 使用的设计原则

### 单一职责原则

- `SocketServer` 只处理传输。
- `RpcRouter` 只处理验证和分发。
- Handlers 只适配协议与业务。
- `AgentTurnService` 编排 Slice 循环与暂停恢复。
- `SessionLifecycleService` 负责 status、discard、reset、archive 和 hard delete。
- `ConversationContextLoader` 负责加载上下文、召回记忆并构造 graph 输入。
- `ExecutionLifecycleService` 负责 Execution begin、resume、pending 判断和准备失败 pause。
- `TurnResultBuilder` 负责把流式事件聚合为最终 JSON-RPC result。
- `TurnCoordinator` 负责 Turn 准备和最终提交协调。
- `CompletedTurnCommitter` 只负责最小业务事务。
- `MaintenanceScheduler` 只负责持久化后台任务分发。
- `ExecutionRecoveryCoordinator` 只负责两个 SQLite 数据库的恢复对账。
- `CoreApp` 只负责组装和生命周期。

### 依赖倒置原则

`AgentHandlers` 依赖 `AgentTurnRunner` 协议，`CoreApp` 依赖 `ManagedAgentService` 和
`CoreTransport` 协议。两个 Agent service 协议集中定义在 `core.agent.contracts`，
测试和未来 Transport 可以注入替代实现。前台 Turn、后台维护和 checkpoint 分别依赖
`StateStore`、`MaintenanceStateStore` 和 `CheckpointStore` 最小能力协议。

### 开闭原则

增加 RPC 方法时，新增 handler 并在组合根注册；Transport 不需要修改。增加新的 Transport 时，实现 `CoreTransport` 接口即可复用 Router 和 handlers。

### 接口隔离原则

Handler 只获得最小 `RequestContext`，不能访问底层 TCP writer。Agent service 不知道 RPC 或连接概念。
后台维护 handler 不能通过前台 `StateStore` 协议调用不属于自己的能力。

### 组合优于继承

`CoreApp` 通过组合 Router、handlers、Transport 和 Agent service 建立应用，没有创建复杂继承树。

## 优点

- 各层职责明确，定位问题更直接。
- TCP Transport 可独立测试和替换。
- Handler 可以使用 fake context 做快速单元测试。
- Agent 服务可脱离 daemon 独立测试。
- 生命周期关闭顺序集中，降低资源泄漏风险。
- 内部 handler 异常不会将详细信息返回客户端。
- CLI 和 Core 入口形式一致：

  ```text
  python -m src.cli
  python -m src.core
  ```

## 代价

- 文件和接口数量增加，理解完整请求链需要跨多个模块。
- 新 RPC 方法需要同时维护 IPC 模型、handler 注册和测试。
- `CoreApp` 作为组合根会集中依赖许多组件，需要避免写入具体业务逻辑。
- 当前仍只有 TCP Transport，Transport 抽象的收益主要体现在边界和测试性。

## 当前功能边界

当前已支持：

- Core daemon 启动和正常 shutdown。
- TCP + NDJSON + JSON-RPC。
- 请求验证与 token 鉴权。
- `core.ping`、`core.shutdown`、`agent.chat`。
- Agent token/step/error/done 流式通知。
- 同 session 串行、跨 session 并行。
- 同步 Agent turn 通过专用有界 executor 执行，不占用 asyncio 默认线程池。
- 最小 Turn 状态与后台维护任务通过同一 `state.db` 事务提交。
- 摘要、记忆和 checkpoint 清理通过持久化任务后台执行。
- 启动时对账 Execution 与 LangGraph checkpoint。
- 启动失败反向清理资源。
- shutdown 等待活跃请求并设置超时。

当前不支持：

- HTTP、WebSocket、Named Pipe 等其他 Transport。
- RPC handler 插件自动发现。
- 任务取消和断线事件续传。
- 多 Core 实例协调。
- 操作系统服务管理。
- 协议版本协商。

## 设计审查结论

本次重构后，Core 主执行链符合单一职责、依赖倒置、接口隔离和组合优于继承原则。

仍存在但不在本次重构范围内的问题：

1. 顶层 Python 包仍命名为 `src`，长期应迁移为正式包名。
2. `state/store.py` 仍然较大，未来可继续按消息、Session 与记忆查询职责拆分。
3. 部署参数已支持用户级 `.env` 与进程环境覆盖。后台维护策略已使用类型化配置并由
   `CoreApp` 注入；其他功能域仍通过兼容 `settings.py` 逐步迁移，当前不支持热更新。
   配置、领域枚举和 Prompt 的边界见
   [`/docs/decisions/configuration-and-domain-constants.md`](/docs/decisions/configuration-and-domain-constants.md)。
4. Agent 流式事件内部 `data` 仍是通用字典，未来可增加严格事件模型。
5. MaintenanceScheduler 当前只有一个 worker，且长任务租约尚未续租；未来多实例前必须加强协调。
