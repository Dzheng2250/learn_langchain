# PR #3 Review 回复

> 文档状态：Historical Review Response
> 本文是对特定历史 Review 的回复，不作为当前功能或配置规范。

感谢对 PR #3 的详细审查。我们逐项复核了建议，并完成了一轮增量可靠性加固。

本轮没有全面异步化 LangGraph、工具和 psycopg Repository，而是在现有同步业务链路外建立了
明确、可配置的异步执行边界。同时补强了数据库事务、Transport 生命周期、异常可观测性和测试。

详细设计与残余风险见
[`pr-3-review-hardening.md`](pr-3-review-hardening.md)。

## 整改结果摘要

- Agent turn 改由专用、有界 `ThreadPoolExecutor` 执行。
- Handler 不再使用无法独立控制容量的 `asyncio.to_thread()` 执行 Agent turn。
- Agent service Protocol 集中管理，同时保留接口隔离。
- 流式通知首次失败后停止重试、记录结构化事件，但不中断 Agent turn。
- 修复 Transport shutdown 的循环等待问题。
- daemon 启停等待时间改为配置项。
- Schema 初始化使用显式事务。
- SQL 文件使用 `sqlparse` 拆分。
- 保留 Workspace 原子 UPSERT，并增加并发注册测试。
- 连接池使用当前 psycopg_pool 版本支持的 `close(timeout)`。
- 完善 Core 关闭失败后的资源清理。

## 逐项回复

### 1. `agent.chat` 同步阻塞与默认线程池容量

**结论：采纳，并修正实施方式。**

已将 `AgentTurnService.run_turn()` 改为异步应用服务接口。同步 LangGraph、工具和数据库链路由
Service 自己持有的专用 `ThreadPoolExecutor` 执行，最大并发由以下配置控制：

```python
CORE_AGENT_WORKERS = 4
```

现有同步执行主体移动到 `_run_turn_sync()`，`stream_turn()` 继续保持同步。`AgentHandlers.chat()`
现在直接执行：

```python
await agent_service.run_turn(...)
```

Service 支持注入外部 Executor；仅关闭自己创建的 Executor。额外使用并发 slot 限制提交数量，
避免 `ThreadPoolExecutor` 的无界等待队列积压 turn。等待协程被取消时，slot 仍会在底层 worker
真正结束后才释放。

当前并发模型：

- 同 Session 由内部 UUID 锁串行执行。
- 不同 Session 最多并行 `CORE_AGENT_WORKERS` 个 turn。
- 本轮不全面异步化 LangGraph、工具和 Repository。

### 2. Workspace 创建时的 UUID 与并发注册

**结论：不采用 SELECT-then-INSERT，保留当前原子 UPSERT。**

当前语句：

```sql
INSERT ...
ON CONFLICT (canonical_path) DO UPDATE ...
RETURNING workspace_id
```

在 PostgreSQL `READ COMMITTED` 下由唯一约束和 `ON CONFLICT` 保证原子并发注册。并发竞争者
可能生成一个未使用的候选 UUID，但不会创建重复 Workspace。

改为“先 SELECT、再 INSERT”会在检查和插入之间引入竞态，除非增加额外重试或锁定逻辑。因此，
我们接受极小的 UUID 生成开销，保留更简单且并发安全的单语句方案。

已增加并发测试，验证多个请求注册同一路径时返回同一 Workspace UUID，数据库仅保留一条记录。

### 3. Agent Protocol 定义分散

**结论：采纳，并保留接口隔离。**

新增统一 contracts 模块：

```text
src/core/agent/contracts.py
```

其中定义：

- `AgentTurnRunner`：供 Handler 使用的最小异步运行接口。
- `ManagedAgentService`：扩展初始化和关闭能力，供 `CoreApp` 使用。

这样既消除了跨文件重复 Protocol，也避免 Handler 依赖不需要的生命周期方法。

### 4. 无效 frame 导致连接断开

**结论：保留当前策略，并补充文档和测试。**

当前 CLI client 是同步、单请求单连接客户端。无效 NDJSON frame 返回 Parse Error 后，只关闭
发送该 frame 的连接；其他并发连接不受影响。

已在 Transport 类、Core 架构文档和 CLI 架构文档中说明该边界，并增加测试验证独立连接仍可
继续请求。

未来若引入长连接或连接池，需要重新设计单连接上的错误恢复策略。

### 5. 数据库连接池关闭

**结论：修正建议后实施。**

当前依赖的 `psycopg_pool 3.3.1` 没有 `wait_closed()`。因此使用：

```python
pool.close(timeout=config.shutdown_timeout_seconds)
```

该方法会等待连接池内部 worker。关闭操作通过 `asyncio.to_thread()` 执行，避免阻塞 Core 事件
循环。

此外，关闭流程现在使用嵌套清理边界。即使 Agent service 关闭失败，事件发布器、数据库连接池
和 PID 文件仍会继续释放。

### 6. `CoreClient` 为同步阻塞客户端

**结论：当前阶段接受，并明确能力边界。**

`CoreClient` 已明确标注为同步、单请求单连接 CLI client。该设计符合当前命令行客户端的执行
模型。

异步 TUI client 将作为独立实现提供，不会直接在异步界面中复用同步 socket client。

### 7. daemon 启停等待时间硬编码

**结论：采纳。**

新增配置：

```python
CORE_DAEMON_STARTUP_TIMEOUT_SECONDS = 15
CORE_DAEMON_STOP_TIMEOUT_SECONDS = 15
```

CLI 的启动和停止轮询现在使用配置值，并增加配置校验与行为测试。

### 8. Schema 初始化事务

**结论：采纳。**

`SchemaManager.initialize()` 现在使用显式：

```python
with conn.transaction():
```

Schema 检测、DDL、版本升级和 migration version 写入均位于同一事务边界中，已移除手动
`commit()`。

当前测试验证显式事务进入和异常退出。真实 PostgreSQL 中部分 DDL 执行后的原子回滚测试需要
一次性数据库和稳定故障注入，因此已记录为有意延期的集成测试。

### 9. 流式事件异常被静默吞掉

**结论：采纳。**

首次 notification 发送失败时会：

- 记录一次 `stream_notification_failed` 结构化事件。
- 停止向已断开的客户端继续发送通知。
- 继续执行已经开始的 Agent turn。

这样既保留“客户端断线不取消 Core 任务”的语义，也避免每个 token 重复记录相同错误。

### 10. SQL 文件使用简单分号拆分

**结论：采纳。**

项目已显式依赖 `sqlparse`，替换 `sql.split(";")`。测试覆盖：

- 字符串中的分号。
- SQL 注释中的分号。
- dollar-quoted 函数体中的分号。

### 11. JSON-RPC request ID 可读性

**结论：不调整。**

当前 UUID 字符串符合 JSON-RPC 规范，具有足够低的碰撞风险，并可稳定关联日志、通知和最终
响应。缩短 ID 主要改善人工阅读，不足以抵消降低标识空间和引入迁移改动的代价。

## 额外发现与修复

整改过程中发现并修复了一个 Transport shutdown 循环等待：

```text
SocketServer.close() 等待连接任务结束
连接任务等待客户端发送下一帧或断开
客户端连接尚未由 close() 关闭
```

关闭顺序已调整为：

```text
停止接受新连接
  -> 关闭客户端 stream，释放空闲 reader
  -> 等待活跃 handler
  -> 超时后取消残留连接任务
  -> 等待 Transport 完全关闭
```

同时为 graceful shutdown 测试增加外层超时，避免同类回归导致测试无限挂起。

## 测试结果

本轮验证结果：

```text
106 tests passed
1 test skipped
```

跳过项是 Windows 环境缺少创建符号链接权限，不涉及本轮变更。

已执行：

```powershell
D:\app\anaconda\envs\agent_learn\python.exe -B -m unittest discover -s tests -v
git diff --check
```

新增或扩展的测试覆盖：

- Agent executor 容量、所有权和取消等待者行为。
- 同 Session 串行与不同 Session 并行。
- notification 失败只记录一次且不取消 turn。
- Core 关闭资源释放和连接池 timeout。
- malformed frame 连接隔离。
- 多连接并发响应。
- graceful shutdown 等待活跃请求。
- Schema 显式事务。
- WorkspaceMigration 校验失败回滚。
- SQL 复杂分号拆分。
- Workspace 并发原子注册。
- daemon 配置化启停 timeout。

## 已知残余风险

以下场景尚未进行完整端到端验证，原因和可靠测试方案已记录在
[`pr-3-review-hardening.md`](pr-3-review-hardening.md#有意延期的测试)：

- Schema 初始化在真实 PostgreSQL 中执行部分 DDL 后的原子回滚。
- Transport shutdown 超时后的残留任务取消和资源清理。
- 真实 TCP 客户端断线后 Agent turn 继续完成。

此外，当前同步 LLM、工具和数据库操作不能被强制取消。shutdown timeout 能限制 Core 等待时间，
但不能终止已经进入第三方同步调用的底层操作。全面异步化和任务取消协议留待后续版本。

## 最终结论

本轮接受并实施了 reviewer 指出的主要可靠性问题，同时对 Workspace 注册和连接池关闭建议按
当前 PostgreSQL 与 psycopg_pool 的实际行为进行了修正。

改动保持了现有双进程架构和同步业务链路，没有引入不必要的全面异步重构。新增的执行边界、
事务边界、关闭语义、测试和文档使系统行为更加明确，也为后续异步 TUI、任务取消和集成测试
基础设施保留了演进空间。
