# PostgreSQL 到本地状态迁移

> 文档状态：Current Specialized Procedure
> 权威范围：旧 PostgreSQL 数据迁移到本地状态的专项操作
> 维护触发：迁移命令、旧 Schema、目标本地 Schema 或校验规则变化

## 本文负责

- 旧 PostgreSQL 数据迁移到本地状态的专项步骤、校验和历史结果标识。

## 本文不负责

- 不作为日常升级流程。
- 不定义目标 Schema；见本地状态 Schema 参考。

>
> 本文包含 2026-06-13 的历史执行结果；历史数量不代表当前数据库状态。

## 迁移目标

本次迁移只保留以下 Workspace 中名为 `default` 的 Session：

```text
D:\Desktop_logo\github\myprojects\learn_langchain
```

与该 Session 无关的其他 Session、消息、长期记忆和事件允许删除。

迁移后的职责：

- SQLite `state.db`：Session、消息、长期记忆的权威来源。
- SQLite `checkpoints.db`：可恢复执行断点。
- PostgreSQL：可选 Telemetry 或未来投影，不再是普通对话的必要依赖。

## 安全流程

迁移命令分为只读检查和正式执行。

### 1. 停止 Core

```powershell
learn-agent stop
learn-agent status
```

迁移命令检测到 daemon 运行时会拒绝执行，防止迁移期间继续产生新消息。

### 2. Dry-run

```powershell
learn-agent-core migrate-local-state `
  --workspace D:\Desktop_logo\github\myprojects\learn_langchain `
  --keep-session default
```

Dry-run 只读取 PostgreSQL，显示保留数量和预计删除数量，不修改 SQLite 或 PostgreSQL。

2026-06-13 的实际 dry-run 结果：

| 数据 | 保留 | 预计删除 |
|---|---:|---:|
| Sessions | 1 | 3 |
| Messages | 503 | 75 |
| Memories | 7 | 6 |
| Events | 1611 | 613 |

正式执行前应以最新 dry-run 为准。

### 本次实际执行结果

2026-06-13 已完成正式迁移和 PostgreSQL 来源清理：

- SQLite：`1` 个 Workspace、`1` 个 Session、`503` 条消息、`7` 条记忆、`1611` 条导入事件。
- SQLite 外键检查：`0` 条违规。
- PostgreSQL：保留 `1` 个 Workspace、`1` 个 `default` Session、`503` 条消息、`7` 条记忆、`1611` 条事件。
- 完整备份：`learn_agent_20260613_193932.dump`，大小 `403925` 字节。

本机历史容器名是 `pgvector2`，与默认值 `learn-agent-postgres` 不同。迁移时通过以下环境变量显式指定：

```powershell
$env:LEARN_AGENT_DB_CONTAINER = "pgvector2"
```

### 3. 正式迁移并清理来源

```powershell
learn-agent-core migrate-local-state `
  --workspace D:\Desktop_logo\github\myprojects\learn_langchain `
  --keep-session default `
  --apply `
  --prune-source
```

`--prune-source` 必须与 `--apply` 一起使用，避免误把 dry-run 变成删除操作。

执行顺序：

1. 使用 `pg_dump` 或 Docker 创建完整 PostgreSQL 备份。
2. 构建临时 SQLite 数据库。
3. 复制 `default` 的 Session、消息、关联记忆和事件。
4. 校验 SQLite 行数和外键。
5. 原子替换正式 `state.db`。
6. 在一个 PostgreSQL 事务中删除非 `default` 数据。
7. 再次校验 PostgreSQL 保留行数；不一致则回滚删除事务。

如果本地已有 `state.db`，迁移前会保存 `state.pre_migration.bak`。

## 记忆保留规则

只保留至少有一条来源消息属于目标 `default` Session 的记忆。

若一条记忆同时关联目标 Session 和其他 Session：

- 记忆本身保留。
- 只保留目标 Session 的来源关系。
- 其他 Session 的来源关系删除。

这避免把无法追溯到保留历史的长期记忆带入新状态。

## 恢复

正式迁移前会生成 PostgreSQL 完整备份。发生问题时：

1. 停止 Core。
2. 保留当前 SQLite 文件用于调查。
3. 使用 `pg_restore` 恢复 PostgreSQL 备份。
4. 将 `state.pre_migration.bak` 恢复为 `state.db`，或删除错误的新状态后重新迁移。

不要在未验证备份可读取时手工删除 PostgreSQL 数据。
