# PR #3 Review 整改与可靠性加固

## 目标

本轮根据 PR #3 review 加固双进程架构，但不全面异步化 LangGraph、工具和 psycopg
Repository。核心策略是为同步 Agent 链路建立明确的异步边界，并修复数据库、Transport
和 daemon 生命周期中可验证的可靠性问题。

## 已实施改进

### 有界 Agent 执行

`AgentTurnService.run_turn()` 现在是异步应用服务接口。它把同步的 LangGraph、工具和
数据库链路提交到专用 `ThreadPoolExecutor`，最大并发由 `CORE_AGENT_WORKERS` 控制。

```text
AgentHandlers
  -> await AgentTurnService.run_turn()
      -> dedicated agent-turn executor
          -> synchronous LangGraph / tools / repositories
```

同一 Session 仍由 UUID 锁串行执行。不同 Session 可以并行，但不会超过执行池容量。
异步并发 slot 同时限制提交到 executor 的 turn 数量，避免标准线程池的无界等待队列
积压 turn。slot 只在底层同步 worker 真正结束后释放，即使等待它的 RPC 协程已取消，
也不会错误放大实际并发。Handler 不再使用无法独立配置容量的 asyncio 默认线程池。

`AgentTurnRunner` 和 `ManagedAgentService` 集中定义在 Agent contracts 模块：

- Handler 只依赖最小异步运行接口。
- `CoreApp` 依赖包含 `initialize()` 和 `close()` 的托管服务接口。

### 断线与关闭

客户端断开后，Core 不取消已经开始的 turn。第一次通知发送失败会记录
`stream_notification_failed`，随后停止向该连接发送通知，避免每个 token 重复报错。

`CoreApp.close()` 在线程中等待同步 Agent service 和连接池关闭，避免事件循环阻塞后与
正在回传通知的 Agent worker 形成死锁。
关闭流程使用嵌套清理边界；即使 Agent service 关闭失败，事件发布器、数据库连接池和
PID 文件仍会继续释放。

当前使用的 `psycopg_pool 3.3.1` 没有 `wait_closed()`。`ConnectionPool.close(timeout)`
本身会等待内部 worker，因此 Core 将 shutdown timeout 显式传给该方法。

### 数据库可靠性

`SchemaManager.initialize()` 使用显式 `conn.transaction()` 包裹结构检测、DDL、版本升级
和迁移版本写入。任何步骤失败时，同一次初始化中的更改会整体回滚。

SQL 文件使用 `sqlparse` 拆分，支持字符串、注释和 dollar-quoted 函数体中的分号。

Workspace 注册继续使用：

```sql
INSERT ... ON CONFLICT (canonical_path) DO UPDATE ... RETURNING workspace_id
```

该单语句 UPSERT 在 PostgreSQL `READ COMMITTED` 下是原子且并发安全的。并发首次注册
可能生成未使用的候选 UUID，但不会产生重复 Workspace。改为 SELECT-then-INSERT 会引入
检查与插入之间的竞态，因此不采用。

### Daemon 与 Transport

daemon 启动和停止等待时间分别由以下配置控制：

```text
CORE_DAEMON_STARTUP_TIMEOUT_SECONDS
CORE_DAEMON_STOP_TIMEOUT_SECONDS
```

`CoreClient` 明确定义为同步、单请求单连接 CLI client。异步 TUI client 留待后续实现。

无效 NDJSON frame 会收到 Parse Error，然后只关闭发送该 frame 的连接；其他连接继续
正常服务。

Transport shutdown 会先停止监听并关闭客户端 stream，使等待下一帧的空闲连接任务退出；
已经进入 handler 的请求不会因此取消，Core 会继续等待其在 shutdown timeout 内完成。

## 未采用的 Review 建议

- 不采用 Workspace SELECT-then-INSERT：会削弱当前原子 UPSERT 的并发安全性。
- 不调用 `pool.wait_closed()`：当前依赖版本不存在该 API。
- 不全面异步化：当前工具、LangGraph 和 Repository 都是同步接口，本轮使用有界执行池
  建立清晰边界。
- 不缩短 JSON-RPC request ID：当前 UUID 字符串符合协议且碰撞风险更低。

## 当前边界

- 执行中的同步 LLM 或工具调用仍不能被强制取消。
- shutdown 会停止接收新连接并等待活跃请求；底层不可取消操作可能超过期望时间。
- Agent executor 只限制 turn 数量，工具内部自行创建的并行任务仍由各工具配置控制。
- CLI client 不支持长连接、连接池、重连或事件续传。

## 验证重点

- 专用 executor 容量限制和外部 executor 所有权。
- 同 Session 串行与跨 Session 并行。
- 客户端断线后 turn 继续且只记录一次通知失败。
- Schema 初始化与迁移校验失败时回滚。
- SQL 中复杂分号的正确拆分。
- Workspace 并发注册返回同一个内部 UUID。
- malformed frame 只影响当前连接。

## 有意延期的测试

本轮自动测试已经覆盖主要代码路径，但以下场景没有用完整端到端测试验证。它们不是被忽略的
需求，而是因为可靠验证需要额外的基础设施或更精确的测试边界，因此明确延期。

### Schema 初始化的真实数据库原子回滚

当前覆盖：

- FakeConnection 验证 `SchemaManager.initialize()` 进入显式事务。
- 初始化异常会从事务上下文退出，不会继续执行后续步骤。
- WorkspaceMigration 校验失败会触发事务回滚路径。

本轮未实现原因：

- Mock 或 FakeConnection 只能证明代码调用了事务接口，不能证明 PostgreSQL 中已经执行的 DDL
  和 migration version 确实全部回滚。
- 直接使用开发数据库进行故障注入可能污染重要数据，测试结果也会依赖本机已有 Schema。
- 可靠测试需要一次性数据库，并且必须能够在指定 DDL 执行后、版本写入前稳定注入失败。

可靠测试方案：

1. 使用 Testcontainers 或独立 Docker PostgreSQL，为每次测试创建一次性数据库。
2. 从空数据库执行 Schema 初始化，并在 `execute_sql_file()` 执行部分 DDL 后注入异常。
3. 使用新的数据库连接查询 `information_schema` 和 `schema_migrations`，避免复用已失败事务连接。
4. 断言部分创建的表、约束和 migration version 均不存在。
5. 移除故障注入后重新初始化，断言 Schema 可以完整创建且只记录一份版本。
6. 测试结束后销毁容器或数据库，不连接开发数据库。

通过标准：

- 故障发生后数据库状态与初始化前一致。
- 重试初始化能够成功。
- 不留下半完成 Schema 或错误版本记录。

### Transport shutdown 超时与残留任务取消

当前覆盖：

- graceful shutdown 会等待活跃 handler 完成。
- shutdown 会先关闭客户端 stream，空闲连接不会造成循环等待。
- 测试自身设置了超时，防止关闭流程再次无限挂起。

本轮未实现原因：

- 当前测试验证正常完成路径，没有构造一个永不返回且能观察取消结果的 handler。
- 简单使用永久等待的协程容易在测试失败时留下后台任务，造成后续测试随机挂起或产生
  `Task was destroyed but it is pending` 警告。
- 可靠测试需要同时验证超时耗时、任务取消和资源清理，不能只验证 `close()` 最终返回。

可靠测试方案：

1. 注册一个进入后等待 `asyncio.Event`、并在收到 `CancelledError` 时设置确认标记的 handler。
2. 通过真实 TCP 连接发送请求，等待 handler 确认已经开始。
3. 使用较短但非零的 shutdown timeout 调用 `SocketServer.close()`。
4. 使用测试框架外层超时防止实现错误导致测试无限挂起。
5. 断言关闭耗时不明显超过配置 timeout、handler 收到取消、连接已关闭、Transport
   的任务和 writer 集合最终为空。
6. 再次调用 `close()`，验证关闭操作幂等。

通过标准：

- 永不结束的 handler 在 timeout 后被取消。
- `close()` 在可预测时间内返回。
- 不残留连接任务、writer 或 asyncio 警告。

### 真实 TCP 客户端断线后 Agent turn 继续执行

当前覆盖：

- Handler 单元测试模拟 notification 发送失败。
- 首次发送失败只记录一次 `stream_notification_failed`。
- 后续通知不再发送，Agent service 仍完成本轮执行。

本轮未实现原因：

- 真实 Agent turn 会调用 LLM、数据库和工具，直接用于断线测试会引入网络、密钥、模型响应时间
  和持久化数据等非确定因素。
- 只关闭 TCP 客户端并不足以证明业务 turn 已完成；测试还需要一个独立于连接生命周期的完成信号。
- Windows 和其他平台对 socket 关闭错误的具体表现可能不同，测试不能依赖单一异常类型。

可靠测试方案：

1. 启动随机端口的真实 `SocketServer`，注入可控的异步 AgentTurnRunner 测试替身。
2. 测试替身在开始和完成时分别设置独立事件，并从工作线程连续发布多个 notification。
3. 客户端发送 `agent.chat`，收到首个 notification 后立即关闭 socket。
4. 等待独立完成事件，断言 Agent turn 在客户端断线后仍然执行完成。
5. 通过测试 EventPublisher 断言只记录一次 `stream_notification_failed`。
6. 断言服务端没有向已断开的连接继续尝试发送后续 notification，并且其他连接仍能执行
   `core.ping`。

通过标准：

- 客户端断线不会取消已经开始的 turn。
- 通知失败事件只产生一次。
- 连接错误不会影响 daemon 或其他客户端。

### 后续实施条件

上述测试建议在建立统一的集成测试基础设施后实施：

- 一次性 PostgreSQL 容器或测试数据库 fixture。
- 随机空闲 TCP 端口 fixture。
- 可观察开始、完成和取消状态的 AgentTurnRunner 测试替身。
- 每个集成测试都有外层超时和强制资源清理。

在这些条件具备前，不应使用开发数据库、真实 LLM 或不可控永久任务来制造“看似端到端”的测试；
这类测试容易不稳定，并可能掩盖而不是发现生命周期问题。
