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
> 端到端请求路径见其中的[完整数据流动示意图](/docs/architecture/agent-execution-call-chain.md#完整数据流动示意图)，
> Agent 内部执行见[Agent 执行架构](/docs/architecture/agent-execution-architecture.md)，双事件通道见[事件系统](/docs/architecture/event-system.md)。
>
> PR #3 review 整改与可靠性决策见
> [`/docs/history/pr-3-review-hardening.md`](/docs/history/pr-3-review-hardening.md)。
>
> PostgreSQL Compose 部署、用户级配置与环境覆盖规则见
> [`/docs/operations/deployment.md`](/docs/operations/deployment.md)。

## 本文负责

本文只解释 Core daemon 的组合根、生命周期和依赖装配：

- `CoreApp` 如何启动、关闭和清理资源。
- `CoreContainer` 如何装配服务、handlers、router 和 transport。
- Transport、Router、Handler 与应用服务之间的依赖方向。
- Core 进程级资源的关闭顺序，例如 transport、worker、maintenance、telemetry、trace 和数据库连接。

## 本文不负责

以下内容由专项文档负责，本文只保留入口链接，不重复定义细节：

- Agent turn、slice、工具调用和预算控制：见
  [Agent 执行架构](/docs/architecture/agent-execution-architecture.md)。
- `state.db`、`checkpoints.db`、事务、Outbox、CAS 和恢复协调：见
  [本地数据库设计与一致性机制](/docs/architecture/database-state-and-consistency.md)。
- Ports、Adapters、Unit of Work 和 DI 边界：见
  [面向接口的 Core 设计](/docs/architecture/interface-driven-core.md)。
- RPC 字段、流式事件和前端接入契约：见 `/docs/api/`。

## Core 职责与依赖方向

Core daemon 负责承载 RPC 服务、Agent 应用服务和进程级资源生命周期。它通过组合而不是继承把
Transport、Router、Handlers 和 Application Services 连接起来。

```text
Transport -> RpcRouter
Handlers  -> RequestContext + Application Service
CoreApp   -> 组装所有组件并管理生命周期
Agent     -> 不依赖 RPC、Transport 或 CLI
```

Core 文档只描述这些进程级边界。Agent 内部协作者和事件语义由 Agent 架构文档负责。

## DI 组合方式

Core 使用 `dependency-injector` 描述进程级依赖图。`CoreApp` 仍然是生命周期入口，
但不再手写所有构造细节，而是从 `CoreContainer` 获取已经装配好的组件。

```text
CoreApp
  -> 创建/接收 CoreContainer
  -> 注入 CoreConfig、auth_token、shutdown_event、trace_recorder
  -> 从容器取得 AgentTurnService、AgentRequestStreamService、SessionLifecycleService、ConversationContextLoader、ExecutionLifecycleService、Router、Handlers、Transport
  -> 按固定顺序 start / close
```

`src/core/container_factories.py` 保存无状态构造辅助函数，例如默认 Socket transport、可选
PostgreSQL pool、EventBus 和 maintenance handler map。它们被拆出是为了让
`CoreContainer` 只描述 provider 图，不再混入大量普通 helper 函数。

选择 `dependency-injector` 的原因：

- 它是成熟的 Python DI 容器，支持 `Singleton`、`Factory`、`Object` 和测试 override。
- 它不要求应用是 HTTP/ASGI 服务，适合当前 TCP/NDJSON/JSON-RPC daemon。
- 它能把对象图集中在组合根，业务服务仍然通过构造函数显式声明依赖。

约束：业务模块不能把容器当 service locator 使用。`AgentTurnService`、
`SessionLifecycleService`、`TurnFinalizer`、handlers 等应用层代码不得 import
`CoreContainer` 或 `dependency_injector`；这些边界由契约测试保护。
同样，业务模块也不应依赖 `container_factories.py`；该模块只属于组合根。


## 当前组件

| 模块 | 职责 |
|---|---|
| `main.py` | 解析 Core 命令、加载配置并启动事件循环 |
| `app.py` / `container.py` | 组合根、依赖装配和进程生命周期 |
| `transport/` | TCP 连接和 NDJSON frame |
| `bus/` | JSON-RPC 验证、鉴权和路由 |
| `handlers/` | 协议参数与应用服务之间的适配 |
| `agent/`、`session/`、`finalization/` | 应用服务，由专项架构文档说明 |
| `ports/`、`adapters/` | 内部接口与具体基础设施实现 |

本表只列稳定模块职责，不复制完整文件树。具体文件位置以源码和
[面向接口的 Core 设计](/docs/architecture/interface-driven-core.md)为准。

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

- 从 `CoreContainer` 取得 Router、handlers、Agent services 和 Transport。
- 注册 `core.*`、`agent.*` 和 `session.*` 公共 handler；方法清单由 RPC 参考维护。
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

## 设计与能力边界

Core 的 Ports、Adapters、Unit of Work、IoC、DI 和设计原则统一由
[面向接口的 Core 设计](/docs/architecture/interface-driven-core.md)维护。

当前公开 RPC、流式事件和协议限制统一由 `/docs/api/` 维护；未实现能力统一登记在
[路线图与已知限制](/docs/product/roadmap-and-known-limitations.md)。

本篇不再复制设计优缺点、完整功能清单或阶段性 review 结论，避免这些内容在代码演进后失效。
