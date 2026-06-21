# 本地状态备份与恢复

> 文档状态：Current
> 权威范围：当前本地优先状态的人工备份、恢复和验证流程
> 维护触发：状态目录、数据库职责、Schema 或备份能力变化

## 本文负责

- 本地状态文件和相关持久化数据的人工备份、恢复与验证。

## 本文不负责

- 不解释数据库设计；见 State Architecture 和 Schema Reference。
- 不承诺尚未实现的自动备份。


## 1. 为什么必须整体备份

当前系统使用两个 SQLite 数据库：

- `state.db` 是业务事实的权威来源。
- `checkpoints.db` 保存未完成 LangGraph 执行的恢复点。

两者不共享一个数据库事务。系统通过 Execution 状态、幂等清理任务和启动对账处理跨库一致性。
因此，人工备份应复制整个本地状态目录，而不是只复制一个数据库文件。

## 2. 备份范围

| 路径 | 建议 | 原因 |
|---|---|---|
| `state.db` | 必须 | 保存 Session、消息、记忆和 Execution |
| `checkpoints.db` | 必须 | 保留未完成任务的恢复能力 |
| `artifacts/` | 必须 | 可能被消息或工具结果引用 |
| `telemetry/` | 可选 | 仅用于观测 |
| `traces/` | 可选 | 仅用于排障 |
| 用户级 `.env` | 单独安全备份 | 包含密钥，不应与普通状态快照一起共享 |
| runtime PID/token/log | 不备份 | 只属于当前 daemon 生命周期 |

本地状态默认位于平台用户数据目录下的 `learn-agent/state/`，或
`LEARN_AGENT_STATE_DIR` 指定的位置。

## 3. 一致备份流程

当前项目没有内置在线快照命令。可靠方式是停止 daemon 后复制目录：

```shell
learn-agent stop
learn-agent status
```

确认 daemon 已停止后，将整个状态目录复制到带时间戳的备份目录。不要遗漏 SQLite 的
`-wal` 或 `-shm` 文件；复制整个目录可以避免人工判断错误。

备份完成后：

1. 确认备份目录非空。
2. 确认至少存在 `state.db`。
3. 记录项目版本、备份时间和原状态目录路径。
4. 重启 Core 并确认健康状态。

## 4. 恢复流程

恢复会替换当前本地状态，必须先保留现场：

1. `learn-agent stop` 并确认 daemon 已停止。
2. 将当前状态目录重命名或复制为调查副本。
3. 将备份整体恢复到原状态目录。
4. 确认文件权限允许当前用户读写。
5. 启动 Core。
6. 检查 `learn-agent status` 和目标 Session 的 `session status`。
7. 发起只读或低风险请求验证历史与恢复状态。

Core 启动时会执行 Schema 检查、迁移和 Execution/checkpoint 对账。若备份中的
`state.db` 与 `checkpoints.db` 来自不同时间点，恢复协调器可能将任务标记为不可恢复；
已提交历史仍应以 `state.db` 为准。

## 5. 恢复验证

至少验证：

- Core 能正常启动。
- 目标 Workspace 和 Session 可解析。
- 历史消息和长期记忆数量符合预期。
- `session status` 不包含无法解释的 pending Execution。
- `maintenance.failed` 没有持续增长。
- 新对话能够完成最小持久化提交。

### 建议的恢复演练与故障注入

备份只有经过恢复演练才可信。应在隔离的临时状态目录和测试代码版本中验证：

| 场景 | 预期结果 |
|---|---|
| 备份来自较旧 Schema | Core 执行支持的加法 Migration 后启动，或明确拒绝不支持的版本 |
| 只有 `state.db`、缺少 `checkpoints.db` | 已提交历史仍可读取；未完成 Execution 可能被标记为 checkpoint 缺失且不可恢复 |
| `state.db` 与 `checkpoints.db` 来自不同时间点 | 启动对账保留业务事实，并明确标记无法恢复的 Execution |
| 恢复目录只读或权限错误 | Core 拒绝启动，日志给出 SQLite 或文件权限错误 |
| 恢复数据包含未知状态值 | Schema Migration/验证拒绝启动，不带着不支持的数据继续运行 |

故障注入应使用备份副本和独立 `LEARN_AGENT_STATE_DIR`，不得破坏真实用户状态。

## 6. PostgreSQL 备份

PostgreSQL 不是普通对话的权威状态。只有启用了 PostgreSQL Telemetry、保留旧数据或准备执行
迁移时才需要备份。

数据库迁移代码会优先使用 `pg_dump`，不可用时可尝试通过配置的 PostgreSQL 容器执行备份。
不要用直接复制 PostgreSQL 数据目录替代逻辑备份，尤其不能在数据库运行时复制。

旧 PostgreSQL 到本地状态的专项流程见
[PostgreSQL 到本地状态迁移](/docs/operations/local-state-migration.md)。

## 7. 风险与当前缺口

- 当前没有自动备份、保留周期和恢复演练调度。
- 当前没有在线 SQLite backup API 或快照 RPC。
- 只备份 `state.db` 会丢失未完成任务恢复点和 Artifact。
- Trace、Telemetry 和 PostgreSQL 投影不能替代业务状态备份。
- 用户级 `.env` 包含密钥，备份介质必须单独保护。
