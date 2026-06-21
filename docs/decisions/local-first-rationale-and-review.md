# 本地优先状态优化：问题、决策与设计审查

> 文档状态：Current Decision
> 权威范围：选择本地优先状态、异步维护与恢复协调的原因和风险
> 维护触发：业务权威存储、最终响应关键路径或一致性方案变化

## 本文负责

- 选择本地优先状态、最小提交、异步维护和恢复协调的原因、风险与代价。

## 本文不负责

- 不维护当前 Schema 和事务流程；见 State Architecture。
- 不作为性能测试报告。


数据库表、事务、维护队列、CAS、Saga 和恢复协调器的集中说明见
[`/docs/architecture/database-state-and-consistency.md`](/docs/architecture/database-state-and-consistency.md)。本文重点解释为什么从用户可感知
卡顿问题推导出本地优先与最小提交设计。

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

### 5.6 决策边界与当前实现的关系

本决策只规定以下约束，不固定具体类名和调用顺序：

1. 权威业务事实优先写入本地状态库。
2. 向用户声明完成前，必须提交本轮不可丢失的最小业务状态。
3. 摘要、记忆提取、checkpoint 清理和投影属于可恢复的后台维护。
4. Telemetry 与 Trace 是 best-effort 观测数据，不参与业务成功判定。
5. 两个 SQLite 数据库之间不伪装成单事务，而是通过状态、幂等任务和恢复协调完成最终一致。

当前实现由各专项文档维护：

- [数据库状态与一致性](/docs/architecture/database-state-and-consistency.md)：权威状态、事务和跨库一致性；
- [响应最终化与 checkpoint 一致性](/docs/architecture/response-finalization-and-checkpoint-consistency.md)：最小提交和后台维护；
- [可恢复执行](/docs/architecture/resumable-execution.md)：Execution、Slice 和恢复；
- [事件系统](/docs/architecture/event-system.md)：非阻塞观测写入；
- [本地状态 Schema](/docs/reference/local-state-schema.md)：表、字段、约束和索引。

## 6. 为什么 Decision 不维护实时关键路径和优化清单

实时函数调用、性能指标和未完成事项变化频繁，不属于架构决策的稳定内容：

- 当前函数调用与提交顺序由 Architecture 文档维护；
- 延迟目标和验证方法由 [非功能需求](/docs/quality/non-functional-requirements.md) 与
  [非功能测试](/docs/quality/non-functional-testing.md)维护；
- 未实现能力和后续优化由[路线图与已知限制](/docs/product/roadmap-and-known-limitations.md)维护。

无论实现如何演进，都必须保持一个耐久性边界：最小业务提交成功后，才能向用户声明本轮已可靠完成。

## 7. 风险与代价

### 7.1 SQLite 单写者限制

SQLite 同一时刻只能有一个写事务。当前通过短事务、Session 锁和 `busy_timeout` 控制风险，但高并发、多用户服务并不适合这一方案。

### 7.2 本地数据风险

- 用户删除本地数据目录会丢失权威状态。
- 本地磁盘损坏会影响 Session。
- 当前依赖用户目录权限，没有加密。
- 应继续增加自动快照与恢复工具。

### 7.3 两个 SQLite 数据库的一致性

业务状态位于 `state.db`，LangGraph checkpoint 位于 `checkpoints.db`。它们无法通过一个 SQLite 事务原子提交。

异常终止时可能出现：

- Execution 状态已更新，但 checkpoint 尚未写完。
- Turn 已完成，但 checkpoint 尚未删除。

当前通过 `ExecutionRecoveryCoordinator`、checkpoint 状态和幂等清理任务实现 Saga 式恢复：

- `running + checkpoint 存在` 转为可恢复暂停。
- 活动 Execution 缺少 checkpoint 时标记为不可恢复，禁止自动续跑。
- 已完成或已丢弃但未清理的 Execution 会补建清理任务。

该方案实现最终一致，但仍不等同于跨库原子事务。

### 7.4 PostgreSQL 投影一致性

未来启用投影后，PostgreSQL 是延迟副本，不保证与 SQLite 实时一致。查询方必须知道数据新鲜度，不能把投影重新当成权威来源。

### 7.5 两种后台队列具有不同可靠性

Telemetry 队列满时会丢弃事件，这是有意的可用性取舍。摘要、记忆和 checkpoint 清理使用
`state.db.maintenance_jobs` 持久化队列，Core 重启后可以继续认领，不能使用相同的 best-effort 策略。

### 7.6 分支与 Artifact 不属于本决策的完成条件

消息分支和大型内容外置可以降低重复存储与提交成本，但它们是独立能力，不应与本地优先状态决策
绑定发布。当前支持范围和后续工作以[路线图与已知限制](/docs/product/roadmap-and-known-limitations.md)为准。
## 8. 是否符合设计模式与设计原则

总体方向符合，但并不代表已经达到最终理想结构。

### 采用的模式及其目的

| 原则或模式 | 在本决策中的用途 |
|---|---|
| Unit of Work | 把本轮不可丢失的业务事实作为一个提交边界 |
| Transactional Outbox | 业务状态与“稍后必须执行”的维护意图同时提交，避免任务丢失 |
| Saga / Process Manager | 用状态与补偿步骤协调无法跨库原子提交的状态库和 checkpoint |
| Repository / Port | 让应用服务依赖领域能力，而不是 SQLite 连接和 SQL |
| Strategy + Handler Registry | 让摘要、记忆和清理任务各自演进，不把维护逻辑堆入 Turn 主流程 |
| Observer | 让 Telemetry 与 Trace 观察业务过程，而不决定业务结果 |
| Composition Root | 在进程入口选择并组装具体存储、调度器和观测适配器 |

这些模式共同贯彻依赖倒置和单一职责：关键路径表达业务提交，后台系统处理派生工作，具体数据库
实现位于适配器边界。当前类与模块关系由[接口驱动的 Core](/docs/architecture/interface-driven-core.md)维护。
### 仍需验证的边界

模式名称不能代替工程验证。当前仍需持续验证 SQLite 写锁争用、后台任务积压、跨库恢复和最小提交延迟；
这些问题的当前状态分别由 Quality 文档和产品路线图维护，而不在 Decision 中复制任务清单。

所以结论是：

> 本决策明确响应提交、派生维护和跨库恢复的边界；具体实现必须持续接受契约测试、恢复测试和延迟基准验证。

## 9. 状态位置为何不在 Decision 中维护

本地优先意味着状态位于用户级数据目录，而不是项目工作目录；具体平台路径、文件名和环境变量属于
运行配置事实，会随平台与配置演进。

- 当前路径与配置项见[配置参考](/docs/reference/configuration-reference.md)；
- 数据文件职责见[本地状态 Schema](/docs/reference/local-state-schema.md)；
- 移动、备份和恢复步骤见[备份与恢复](/docs/operations/backup-and-restore.md)和
  [升级与回滚](/docs/operations/upgrade-and-rollback.md)。

不要在 daemon 运行时直接移动状态文件。