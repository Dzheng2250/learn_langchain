# 本地状态数据库 Schema 参考

> 文档状态：Current
> 权威范围：`state.db` 的核心表关系、字段职责、外键、索引和 Session 删除语义
> 维护触发：本地 Schema、约束、索引或数据归属变化

本文是本地状态 Schema 的事实参考。为什么使用两个 SQLite、如何提交 Turn、Outbox/CAS/Saga 如何工作，
见 [本地数据库设计与一致性机制](/docs/architecture/database-state-and-consistency.md)。

## 本文负责

- `state.db` 核心表和关系。
- Session、Message、Execution、Memory 和 Maintenance 数据归属。
- 外键、复合约束和索引的作用。

## 本文不负责

- 不解释事务、后台维护和跨数据库恢复流程；见数据库一致性架构文档。
- 不解释 Agent 如何执行；见 [Agent 执行架构](/docs/architecture/agent-execution-architecture.md)。
- 不定义 RPC 或用户命令；见 `/docs/api/`。

## 1. `state.db` 的数据模型

### 1.1 总体关系

```mermaid
flowchart LR
    W[workspaces] --> S[sessions]
    S --> B[branches]
    S --> M[messages]
    S --> E[executions]
    E --> ES[execution_slices]
    E --> TL[tool_ledger]
    W --> LM[memories]
    LM --> MS[memory_sources]
    M --> MS
    S --> J[maintenance_jobs]
    E --> J
```

### 1.2 身份与对话历史

| 表 | 作用 | 为什么存在 |
|---|---|---|
| `workspaces` | 将规范化项目路径映射为稳定 UUID | 隔离不同项目的 Session、记忆和工具 |
| `sessions` | 保存 Workspace 内的一段对话及有限上下文 | 允许快速构造下一轮模型输入 |
| `branches` | 保存消息历史分支和分支头 | 为未来从历史消息派生新分支预留结构 |
| `messages` | 保存完整、不可丢失的消息历史 | 用于恢复、审计、记忆来源和未来分支操作 |

关键设计：

- `UNIQUE(workspace_id, session_name)`：每个 Workspace 可以有自己的 `default`，但同一 Workspace
  内不能出现两个同名 Session。
- `messages.parent_message_id`：消息不只是线性序号，也可以组成分支链。
- `messages.raw`：保留可恢复的 LangChain 消息结构；`content` 用于普通查询和审计。
- `messages.execution_id`：能追踪一条消息由哪次 Execution 产生。
- `sessions.recent_messages`：历史兼容列名；当前保存最近完整 Turn 的原始消息缓存，不等同于完整历史。

### 1.3 Session 生命周期：可用、归档与硬删除

Session 不是只有“存在”和“不存在”两种状态。当前实现区分两种删除语义：

| 操作 | 数据库行为 | 适用场景 |
|---|---|---|
| 归档 Session | 设置 `sessions.archived_at`，清除 `pending_execution_id`，保留消息、Execution、任务计划和维护记录 | 默认删除方式；不再继续使用这个 Session，但仍希望保留审计与历史 |
| 硬删除 Session | 删除 `sessions` 行，并通过外键级联删除关联数据 | 明确不再需要该 Session 的所有本地历史 |

`archived_at` 是软删除标记。它的作用不是隐藏一条消息，而是让整个 Session 变为不可继续对话的历史记录：

- `chat`、`resume` 和 `discard` 不会继续操作已归档 Session。
- `session.status` 会返回 `status=archived`，不会自动创建同名新 Session。
- 归档不会删除 `messages`、`executions`、`execution_tasks` 或 `maintenance_jobs`，因此仍可用于审计、排障和未来历史查看。
- 如果归档前存在待恢复 Execution，Core 会先将其标记为已丢弃，并把 checkpoint 清理任务写入 `maintenance_jobs`。

硬删除才是真正的数据清除。删除 `sessions` 行后，数据库外键会级联删除：

- `branches`
- `messages`
- `executions`
- `execution_slices`
- `execution_tasks`
- `execution_task_dependencies`
- 绑定该 Session 的 `maintenance_jobs`

硬删除还会尽力删除对应的 LangGraph checkpoint thread。checkpoint 位于独立的 `checkpoints.db`，不属于同一个 SQLite
事务；如果 checkpoint 文件清理失败，`state.db` 中的 Session 删除仍然以本地业务事实为准。这一点与本项目的
Saga / 恢复协调原则一致：跨数据库清理是可重试的辅助动作，不应反过来阻止权威业务状态变更。

设计上默认使用归档而不是硬删除，是为了避免误操作导致无法追溯。只有用户明确传入 `hard_delete=true` 或 CLI
`--hard` 时，系统才执行不可恢复的数据删除。

### 1.4 短期上下文、摘要窗口与血统

`sessions` 中与上下文有关的字段：

| 字段 | 含义 |
|---|---|
| `active_context_window_id` | 当前 Session 正在使用的上下文窗口 |
| `summary` | 兼容缓存；不再是摘要权威来源 |
| `recent_messages` | 历史兼容列名；当前保存最近完整 Turn 的原始消息缓存 |
| `turn_index` | 已成功提交的最后一轮编号 |
| `summary_through_turn` | 兼容缓存；当前权威值来自 active context window |
| `version` | Session 发生过多少次状态更新，供诊断和未来并发控制使用 |
| `archived_at` | 为空表示 Session 可继续使用；非空表示已归档，只可查询状态，不可继续对话 |

`context_windows` 保存不可变的摘要血统。每次后台压缩成功都会插入一个新窗口，并把
`sessions.active_context_window_id` 指向它。旧窗口不会被覆盖，因此可以追溯“这次摘要基于哪个上一代摘要、覆盖了哪段 turn”。

当前 v1 只实现单 Session 的线性压缩血统。`context_windows.branch_id` 是为后续分支级窗口预留的字段，目前不会参与窗口路由；如果未来支持从旧 turn 派生多个活跃分支，需要把 `active_context_window_id` 从 `sessions` 迁移到 `branches`，或新增 `branch -> active_window` 映射表。

| 字段 | 含义 |
|---|---|
| `window_id` | 当前窗口 ID |
| `first_window_id` | 同一条线性压缩链的根窗口 |
| `previous_window_id` | 上一代窗口；根窗口为空 |
| `summary_text` | 该窗口使用的摘要文本 |
| `summary_through_turn` | 该摘要已经覆盖到哪一轮 |
| `compacted_from_turn` / `compacted_through_turn` | 本次窗口新增压缩的 turn 区间 |
| `opened_at_turn` | 窗口创建时 Session 已完成到哪一轮 |
| `closed_at_turn` | 下一代窗口取代它时关闭到哪一轮 |
| `source_message_count` | 本次压缩读取的原始消息数量 |

前台构造 prompt 时使用：

```text
active context window 的 summary_text
  + messages 中 turn_index > summary_through_turn 的原始消息 tail
  + 当前检索到的长期记忆
  + 当前用户输入
```

后台摘要 CAS 不再只比较 `sessions.summary_through_turn`，而是要求
`sessions.active_context_window_id` 仍等于任务开始时读取到的窗口 ID。这样旧维护任务即使晚完成，也只能被拒绝，不能覆盖新的上下文窗口。

### 1.5 可恢复执行

| 表 | 作用 |
|---|---|
| `executions` | 保存一项可能跨多个 Slice 的工作状态 |
| `execution_slices` | 保存每次有界 LangGraph 运行的预算和停止原因 |
| `tool_ledger` | 按 `execution_id + tool_call_id` 保存工具 durable claim、重放策略、状态和精确结果 |
| `tool_approval_requests` | 保存与 Execution/tool_call 绑定的待审批请求及处理结果 |
| `tool_permission_rules` | 保存 Session 或 Workspace 范围的 allow/deny 规则 |
| `tool_approval_audit` | 保存不可变的审批响应审计记录 |

Schema v12 增加审批模式元数据：`sessions.tool_approval_mode` 保存可为空的 Session override；`tool_approval_requests.approval_mode` 固化请求创建时模式；`tool_approval_audit.decision_source` 与 `approval_mode` 区分用户、自动、Hook 和旧记录。模式名不使用 SQL `CHECK`，因此新增注册策略不需要数据库迁移；应用层负责验证，未知值按 `manual` 安全处理。

Schema v13 将预留的 `tool_ledger` 升级为执行幂等账本，并为 `executions` 增加 `resume_policy`、
`pause_fingerprint`、`repeated_pause_count` 和 `pause_metadata`。工具在副作用前写入 `running`，返回后立即保存
精确 `ToolMessage`；大结果写入 ArtifactStore。daemon 启动时遗留 `running` 变为 `uncertain`，不能盲目重放。
v13 可离线回滚到 v12；回滚会丢弃精确结果、对账状态和暂停指纹，只保留旧版预览账本字段。

`executions.status` 表示业务执行状态，例如：

- `running`：正在执行。
- `paused_budget`：达到本次预算边界，可以恢复。
- `paused_error`：发生错误后暂停。
- `paused_confirmation`：等待外部确认。
- `paused_confirmation` 的 `stop_reason=tool_approval` 表示 LangGraph checkpoint 正等待 `approval.resolve` 的恢复值。
- `paused_recovery`：Core 重启后发现 checkpoint 仍存在，等待恢复。
- `unrecoverable_checkpoint`：业务状态认为任务未完成，但 checkpoint 已丢失。
- `completed`：任务已完成。
- `discarded`：用户明确放弃。

`checkpoint_state` 单独描述 Execution 与 checkpoint 的关系：

```text
uninitialized -> available -> cleanup_pending -> cleaned
                       \-> missing
```

业务状态和 checkpoint 状态分开，是因为“任务是否完成”和“断点文件是否已清理”是两个不同事实。

### 1.6 长期记忆

| 表 | 作用 |
|---|---|
| `memories` | 保存 Workspace 级稳定知识 |
| `memory_sources` | 记录一条记忆来自哪些原始消息 |

`memory_sources` 是多对多关系。一条记忆可能由多条消息支持，一条消息也可能支持多条记忆。来源关系
用于追踪和审计，不代表每次加载记忆都要加载所有来源消息。

### 1.7 后台维护与投影

| 表 | 作用 | 当前状态 |
|---|---|---|
| `maintenance_jobs` | 可靠执行摘要、记忆提取和 checkpoint 清理 | 已使用 |
| `projection_outbox` | 未来把本地业务变化投影到 PostgreSQL | 已预留，默认不启用 |
| `imported_events` | 保存从旧系统迁移来的事件 | 迁移兼容用途 |

`imported_events` 是一次性迁移快照，不接收 Core 运行期事件。实时结构化 Telemetry 位于独立的
`state/telemetry/events.db.telemetry_events`。它与权威 `state.db` 分离，避免高频诊断写入
争用 Session/Execution 的提交锁；该数据库损坏或写入失败也不得影响 Agent 业务结果。

`maintenance_jobs` 和 `projection_outbox` 不能合并：

- `maintenance_jobs` 会改变本地派生状态，例如写入摘要、记忆或清理 checkpoint，失败后需要在本机重试。
- `projection_outbox` 只描述“把已经存在的本地事实复制到外部查询库”，外部失败不能改变本地业务结果。

两者的消费者、失败语义和保留策略不同，分表可以避免未来投影故障阻塞本地维护。

`maintenance_jobs` 的关键字段：

| 字段 | 含义 |
|---|---|
| `job_type` | 选择哪个 handler 执行 |
| `dedupe_key` | 幂等去重键，防止同一任务重复入队 |
| `priority` | 数字越大越先执行；checkpoint 清理优先级最高 |
| `status` | `pending`、`running`、`succeeded` 或 `failed` |
| `attempts / max_attempts` | 已尝试次数和最大次数 |
| `next_attempt_at` | 失败后何时允许重试 |
| `lease_expires_at` | worker 对任务的临时所有权何时过期 |
| `last_error` | 最后一次失败摘要 |

## 2. 数据库约束为什么重要

应用代码会犯错，数据库约束是最后一道防线。

### 2.1 外键

SQLite 连接都会执行 `PRAGMA foreign_keys=ON`。例如：

- 删除 Session 时，其 Branch、Message 和 Execution 会级联删除。
- `memory_sources` 不能引用不存在的记忆或消息。
- `maintenance_jobs` 不能绑定不存在的 Session。

### 2.2 Workspace 复合外键

Session、Branch、Message 和 Execution 的主要关系同时携带 `workspace_id + session_id`，用于防止把
A 项目的 Session 数据错误关联到 B 项目。

当前 `memory_sources` 的记忆侧使用 `workspace_id + memory_id` 复合外键，但消息侧只通过全局唯一的
`message_id` 外键关联。应用代码只会传入当前 Session 的消息，因此正常路径保持 Workspace 隔离；不过
数据库 Schema 尚未在消息侧强制验证相同 `workspace_id`。后续应为 `messages` 增加适合的复合唯一约束，
并将 `memory_sources` 消息侧升级为复合外键。

### 2.3 索引

索引用于避免数据增长后每次查询都扫描整张表：

- `idx_messages_session`：按 Session 和轮次读取消息。
- `idx_executions_session`：查找 Session 的执行记录。
- `idx_memories_workspace`：按 Workspace 检索重要记忆。
- `idx_maintenance_jobs_ready`：按状态、执行时间和优先级认领任务。
- `idx_maintenance_jobs_session`：查询一个 Session 的待处理和失败任务数量。

## 3. Tool 审批 Schema 升级与回滚

Schema v8 引入 `tool_approval_requests`、`tool_permission_rules` 和 `tool_approval_audit`。Schema v9 修复增量升级数据库中 `tool_permission_rules` 缺少 Session 复合外键的问题，并在迁移时删除无法匹配现有 Session 的孤立 Session 规则；Workspace 级规则不受影响。Schema v10 为 `tool_approval_audit(request_id)` 增加唯一索引，防止同一个审批请求生成重复审计记录。

升级由 `LocalStateDatabase.initialize()` 在单个事务内自动完成。升级失败时整个初始化事务回滚，旧数据库仍保持可用。

不支持把已经由新版本写入的数据库直接交给只识别 v7 的旧 Core。需要回滚程序版本时：

1. 停止 daemon，并备份完整 `state.db` 和 checkpoint 数据库。
2. 优先恢复升级前备份，这是唯一保留审批历史的无损方案。
3. 若只需要从 v10 回到 v9，可在离线副本中删除唯一索引并移除 v10 迁移记录；审批表和审批数据不需要删除。
4. 若明确接受丢弃全部 Tool 审批记录，可在离线副本中依次删除三个 Tool 审批表，并删除 `local_schema_migrations` 中版本 8、9、10 的记录。
5. 使用目标旧版本启动前运行 SQLite `PRAGMA foreign_key_check` 和完整测试。

仅从 v10 回到 v9 的离线清理：

```sql
DROP INDEX IF EXISTS idx_tool_approval_audit_request;
DELETE FROM local_schema_migrations WHERE version = 10;
```

丢弃全部 Tool 审批数据的离线清理：

```sql
DROP TABLE tool_approval_audit;
DROP TABLE tool_permission_rules;
DROP TABLE tool_approval_requests;
DELETE FROM local_schema_migrations WHERE version IN (8, 9, 10);
```

### Schema v12 离线回滚到 v11

v12 只增加审批模式与决策来源列，不删除请求、规则或审计事实。停止 daemon 并完成备份后，可使用受控命令：

```shell
learn-agent-core rollback-local-state --from-version 12 --to-version 11 --apply
```

该转换删除 `sessions.tool_approval_mode`、`tool_approval_requests.approval_mode`、`tool_approval_audit.decision_source` 和 `tool_approval_audit.approval_mode`，并移除 v12 迁移记录。回滚会丢失 Session 模式 override 和审计来源标签；请求响应、持久权限规则及 v11 资源活动账本继续保留。恢复 v11 Core 后，所有审批按旧版人工语义处理。

## `resource_activities`

保存 Agent Execution 的资源读取和变更元数据。主键为应用生成的 `activity_id`，
`(execution_id, sequence)` 唯一。`event_key` 为一次 Tool Call 内单项资源事实提供幂等键，
并由 `(execution_id, event_key)` 部分唯一索引防止重试重复记账；一次 Tool Call 仍可合法产生多条活动。
主要字段包括 `resource_uri`、`operation`、`observation_mode`、`change_state`、`slice_id`、字节/范围/哈希、
`evidence_status` 与关联 activity IDs。MOVE 使用共享 `change_group_id` 保留源、目标两条资源事实，汇总为一个逻辑变更。

## `resource_activity_counters`

按 Execution 保存已记录和因上限丢弃的活动数量，用于稳定报告 `truncated`。
### Schema v11 离线回滚到 v10

v11 只新增派生的资源活动账本，不修改会话、消息或 Execution 权威数据。优先恢复升级前备份；若明确接受丢弃资源活动审计，应停止 daemon 后执行：

```shell
learn-agent-core rollback-local-state --from-version 11 --to-version 10 --apply
```

命令会先获取 `state.db.operation.lock`，验证只执行受支持的 `v11 -> v10` 转换，再通过原子排他创建生成唯一命名的完整数据库备份，并让备份复制与 SQLite `quick_check` 分别受 30 秒截止约束。备份失败时会删除不完整文件，且不会执行降级。验证成功后再执行与下列 SQL 等价的事务：

```sql
PRAGMA foreign_keys = ON;
BEGIN IMMEDIATE;
DROP TABLE IF EXISTS resource_activities;
DROP TABLE IF EXISTS resource_activity_counters;
DELETE FROM local_schema_migrations WHERE version = 11;
COMMIT;
PRAGMA foreign_key_check;
```

完成后必须使用 v10 Core 启动并运行完整测试。该过程会永久丢弃资源活动历史，但不删除 Session、Message、Execution、审批或 Hook 数据。
