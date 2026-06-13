# 本地优先状态优化：问题、决策与设计审查

## 1. 我们最初遇到了什么问题

最直接的用户体验问题是：

```text
AI 已经输出完回答
    ↓
CLI 一段时间没有恢复输入
    ↓
用户感觉程序卡住
```

最初容易把问题概括成“每轮都写 PostgreSQL，所以数据库太慢”。这个判断只说对了一部分。

真实问题是：**回答生成完成后的多个收尾操作仍处于用户等待的关键路径中**。

旧链路大致包含：

1. 归档完整消息。
2. 更新 Session 的摘要、近期消息和 `turn_index`。
3. 可能调用 LLM 压缩上下文。
4. 可能提取并保存长期记忆。
5. 写入 Telemetry。
6. 等待 PostgreSQL 网络连接、连接池和事务提交。
7. 所有工作完成后，才向 CLI 返回最终响应。

因此，问题不是单纯的“PostgreSQL 性能差”，而是以下设计问题叠加：

- 远程数据库位于普通对话的必要路径。
- 多种职责共享一个 Turn 收尾阶段。
- 普通业务状态、观测事件和长期分析数据没有明确优先级。
- 大任务达到步数限制后缺少可恢复断点。
- 完整历史只有线性归档模型，不利于后续分支和版本控制。

## 2. 优化目标如何从这个问题推导出来

仅仅把 PostgreSQL 写入放到后台并不充分。

如果 Core 在消息尚未可靠保存时立即允许同一 Session 开始下一轮，下一轮可能读到旧状态；如果进程此时崩溃，用户已经看到的回答也可能永久丢失。

因此，本轮确定了四个目标：

1. **移除远程依赖**：普通对话不能依赖 PostgreSQL 可用性和网络延迟。
2. **缩短必要提交**：必须同步完成的保存应是本地、短事务、原子提交。
3. **降低非关键工作优先级**：Telemetry、普通长期记忆提取和未来 PostgreSQL 投影不得阻塞 token 输出。
4. **保存未完成进度**：大任务达到单次执行边界后应暂停和恢复，而不是从头开始。

这些目标共同引出了本地优先状态、后台 Telemetry、可恢复执行和多维工具预算，而不是单独引出了“使用 SQLite”。

## 3. 为什么选择 SQLite

SQLite 适合当前单用户、单机 daemon 架构，主要原因是：

- 数据与 Core 在同一台机器，不需要网络往返。
- 不需要单独部署数据库服务。
- 支持事务、外键和索引，不是简单文本缓存。
- WAL 模式允许读取与短写事务更好地并行。
- 单文件便于备份、迁移和本地调试。
- 适合作为 Session、消息和记忆的单机权威来源。

SQLite 的作用是：

```text
远程、服务型、可能不可用的必要写入
                ↓
本地、短事务、进程内可控的必要写入
```

`journal_mode` 属于数据库级配置，Core 每次启动只配置一次；普通短连接不重复执行 WAL 切换，
避免给最小提交增加额外锁竞争。

## 4. SQLite 是否保证不会卡顿

**不保证。**

SQLite 只是减少了网络、连接池和服务部署带来的不确定延迟。以下情况仍会产生卡顿：

- 本地磁盘很慢或被安全软件扫描。
- 写事务持锁时间过长。
- 多个 Session 同时产生大量写入，争用 SQLite 的单写者能力。
- 最小业务提交需要序列化和写入大量消息。
- 大量消息序列化或大型内容尚未转为 Artifact。
- 后台维护错误地持有 `state.db` 写事务执行慢操作。

因此，正确结论是：

> SQLite 降低了普通持久化延迟和外部依赖风险，但真正决定体验的是关键路径设计，而不是数据库品牌。

## 5. 真正改善延迟的设计改动

### 5.1 最小业务提交与原子 Outbox

`CompletedTurnCommitter.commit()` 在一个 `state.db` 事务中完成：

- 追加本轮完整消息并关联 Execution。
- 更新近期上下文、`turn_index` 和消息分支头。
- 完成 Slice 与 Execution，清除 `pending_execution_id`。
- 写入后续必须执行的 `maintenance_jobs`。

摘要、记忆提取和 checkpoint 删除不在该事务中执行。事务只保存不可丢失的事实和“之后必须做什么”，
避免“回答已声明成功但消息未保存”以及“Execution 已完成但清理任务丢失”。

### 5.2 Telemetry 后台批处理

JSONL 和可选 PostgreSQL Telemetry 通过 `BufferedEventSink` 写入：

- 业务线程只调用 `put_nowait()`。
- 后台线程按数量或时间批量写入。
- 队列满时允许丢弃观测事件，不阻塞 Agent。

Telemetry 是 best-effort 观测数据，不应决定业务结果。

### 5.3 PostgreSQL 从必要依赖变为可选投影

Session、消息和记忆以本地 SQLite 为准。PostgreSQL 可用于未来查询或同步，但不再参与普通对话提交。

这消除了 PostgreSQL 暂时不可用时整个 Agent 无法工作的耦合。

### 5.4 可恢复执行

大任务不再依赖一次图循环完成。系统通过：

- `Execution`
- `Grant`
- `Slice`
- LangGraph checkpoint

保存未完成进度。达到步数或预算边界后可以恢复，而不是重新开始。

### 5.5 大内容独立存储基础

ArtifactStore 已提供内容哈希、压缩、引用和显式垃圾回收。它为以后避免在消息和日志中反复存储大型工具结果提供了基础。

当前尚未自动把所有大型工具输出转为 Artifact。

### 5.6 设计决策与代码位置

| 设计职责 | 主要代码 | 当前作用 |
|---|---|---|
| 用户级状态路径 | `src/config/paths.py` | 计算 `state.db`、`checkpoints.db`、Artifact 和 Telemetry 路径 |
| SQLite 连接与事务 | `src/core/state/database.py` | 配置连接、WAL、外键和短事务 |
| Turn 准备与收尾编排 | `src/core/agent/coordinator.py::TurnCoordinator` | 准备有界上下文并委托最小提交 |
| Session 原子提交 | `src/core/finalization/committer.py::CompletedTurnCommitter` | 在同一事务中提交消息、Session、Execution 和维护任务 |
| 后台维护 | `src/core/maintenance/` | 持久化认领、重试摘要、记忆和 checkpoint 清理任务 |
| 上下文压缩策略 | `src/core/context/manager.py::AgentContextManager` | 快速构建近期上下文；摘要模型只由后台 handler 调用 |
| 跨库恢复 | `src/core/maintenance/recovery.py::ExecutionRecoveryCoordinator` | 对账 Execution 与 LangGraph checkpoint |
| 后台观测写入 | `src/core/telemetry/` | 通过有界队列批量写入 JSONL 或可选 Sink |
| 大内容存储 | `src/core/artifacts/` | 按内容哈希保存压缩后的大型载荷 |

## 6. 当前真实关键路径

当前代码中，模型产生的 token 会持续发送给 CLI。LangGraph 完成后，Core 只同步执行最小业务提交：

```text
最后一个可见 token
  -> extract_turn_messages()
  -> build_fast_state()，不调用 LLM
  -> CompletedTurnCommitter.commit()
       -> 保存消息、Session、Execution
       -> 同事务写入 maintenance_jobs
  -> 发送最终 done
  -> CLI 恢复输入
  -> 后台摘要、记忆提取、checkpoint 清理
```

所以当前实现是：

- token 流不被普通数据库和 Telemetry 写入阻塞。
- PostgreSQL 不再影响普通对话。
- 最终 `done` 只等待必要的本地最小提交。
- 上下文摘要、普通与显式记忆提取、checkpoint 清理不会延迟最终响应。
- 显式“记住”返回 `pending`，不能把任务入队误报为保存成功。

该设计有意保留以下耐久性边界：

```text
最小业务提交完成后，才能向用户声明本轮已可靠完成
```

## 7. 后续延迟优化边界

后续优化不应把完整消息保存也改为 best-effort 后台任务。更可靠的方向是：

1. 对大型消息进行 Artifact 外置，缩短最小提交的序列化和写入时间。
2. 记录 `minimal_commit_latency_ms` 与 `response_release_latency_ms` 的 p95/p99。
3. 避免后台 handler 在调用 LLM 或删除文件时持有 SQLite 写事务。
4. 为维护任务增加保留、清理和必要时的多 worker 隔离策略。
5. 当最小提交持续超过目标时，调查磁盘、消息大小和写锁争用，而不是提前返回成功。

## 8. 风险与代价

### 8.1 SQLite 单写者限制

SQLite 同一时刻只能有一个写事务。当前通过短事务、Session 锁和 `busy_timeout` 控制风险，但高并发、多用户服务并不适合这一方案。

### 8.2 本地数据风险

- 用户删除本地数据目录会丢失权威状态。
- 本地磁盘损坏会影响 Session。
- 当前依赖用户目录权限，没有加密。
- 应继续增加自动快照与恢复工具。

### 8.3 两个 SQLite 数据库的一致性

业务状态位于 `state.db`，LangGraph checkpoint 位于 `checkpoints.db`。它们无法通过一个 SQLite 事务原子提交。

异常终止时可能出现：

- Execution 状态已更新，但 checkpoint 尚未写完。
- Turn 已完成，但 checkpoint 尚未删除。

当前通过 `ExecutionRecoveryCoordinator`、checkpoint 状态和幂等清理任务实现 Saga 式恢复：

- `running + checkpoint 存在` 转为可恢复暂停。
- 活动 Execution 缺少 checkpoint 时标记为不可恢复，禁止自动续跑。
- 已完成或已丢弃但未清理的 Execution 会补建清理任务。

该方案实现最终一致，但仍不等同于跨库原子事务。

### 8.4 PostgreSQL 投影一致性

未来启用投影后，PostgreSQL 是延迟副本，不保证与 SQLite 实时一致。查询方必须知道数据新鲜度，不能把投影重新当成权威来源。

### 8.5 两种后台队列具有不同可靠性

Telemetry 队列满时会丢弃事件，这是有意的可用性取舍。摘要、记忆和 checkpoint 清理使用
`state.db.maintenance_jobs` 持久化队列，Core 重启后可以继续认领，不能使用相同的 best-effort 策略。

### 8.6 分支与 Artifact 尚未完整接入

数据结构已预留，但以下功能仍未完成：

- CLI 创建、切换和合并消息分支。
- 自动把大型工具输出转为 Artifact。
- Artifact 引用与消息删除的完整生命周期联动。

## 9. 是否符合设计模式与设计原则

总体方向符合，但并不代表已经达到最终理想结构。

### 符合的部分

| 原则或模式 | 当前实现 |
|---|---|
| 组合根 | `CoreApp` 统一创建并连接状态库、checkpoint、Agent 服务和 EventBus |
| Repository | `LocalWorkspaceRepository`、`ExecutionRepository` 隔离持久化细节 |
| Facade | `LocalStateStore` 为 Agent 提供统一 Session/消息/记忆接口 |
| Unit of Work | `CompletedTurnCommitter` 原子提交本轮业务事实 |
| Transactional Outbox | `maintenance_jobs` 与 Turn 状态同事务写入 |
| Saga / Process Manager | `ExecutionRecoveryCoordinator` 对账两个 SQLite 数据库 |
| Strategy + Handler Registry | `MaintenanceScheduler` 按任务类型分发独立 handler |
| Factory + Registry | `WorkspaceRuntimeFactory` 和 `WorkspaceRuntimeRegistry` 创建并缓存 Workspace 运行时 |
| Strategy 思想 | PostgreSQL 从权威存储降为可选投影方向；工具按风险类别采用不同预算 |
| Observer | EventBus 与 Sink 将观测逻辑和业务逻辑分离 |
| Dependency Inversion | Agent 服务通过注入的 repository、store factory、runtime registry 工作 |
| 单一职责 | state、checkpoint、artifact、execution、telemetry 分模块管理 |

### 仍需改进的部分

- `AgentTurnService` 仍负责 Slice 循环、暂停和恢复编排，后续可继续提取独立 Execution 协调器。
- 维护队列当前只有一个 worker；长摘要虽然不影响前端响应，但会延后其他普通维护。
- 维护任务租约当前不续租；若未来允许多个 Core 实例，必须增加租约心跳或更严格的单实例约束。
- PostgreSQL projection outbox 已预留，但真正的 projector 尚未实现。
- `succeeded` 维护任务尚无自动保留与清理策略。

所以结论是：

> 本次重构明确了响应提交、派生维护和跨库恢复的边界；仍需通过长期运行和延迟基准验证其工程参数。

## 10. SQLite 数据存储在哪里

当前 Windows 环境的实际路径为：

```text
C:\Users\Dzheng\AppData\Local\learn-agent\state\
```

其中：

```text
state.db        Session、消息、分支、长期记忆、Execution 元数据
checkpoints.db  LangGraph 可恢复执行断点
artifacts\      大型内容存储
telemetry\      默认 JSONL 观测事件
```

路径由 `src/config/paths.py` 中的以下函数决定：

- `local_state_dir()`
- `local_state_db()`
- `checkpoint_db()`
- `artifact_dir()`
- `telemetry_dir()`

可以使用环境变量覆盖整个状态目录：

```powershell
$env:LEARN_AGENT_STATE_DIR = "D:\learn-agent-state"
```

修改后应停止 daemon、迁移或复制数据，再重新启动；不要在 daemon 运行时直接移动数据库文件。
