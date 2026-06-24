# Workspace PostgreSQL 迁移历史设计

> 文档状态：Historical
> 权威范围：本地优先状态启用前的 PostgreSQL Schema 与迁移方案记录
> 维护触发：仅在补充历史背景或修正历史事实时更新

本文不代表当前状态架构。当前 Workspace 隔离决策见
[Workspace 隔离设计决策](/docs/decisions/workspace-isolation-and-migration.md)；当前本地状态迁移操作见
[PostgreSQL 到本地状态迁移](/docs/operations/local-state-migration.md)。

## 历史 PostgreSQL 数据库结构

以下结构用于解释旧数据来源及迁移，不再是普通对话的权威状态模型：

```text
agent_workspaces
  -> agent_sessions
       -> agent_messages
       -> agent_events

agent_workspaces
  -> agent_memories
       -> agent_memory_sources(workspace_id) -> agent_messages
```

主要约束：

- `UNIQUE(workspace_id, session_name)`
- 消息和 Session 事件同时保存 Workspace 与 Session UUID
- 长期记忆查询、更新和去重必须包含 `workspace_id`
- `agent_memory_sources` 使用关系表保存记忆来源，并通过 Workspace 复合外键阻止
  记忆关联其他 Workspace 的消息

## 历史 PostgreSQL 迁移与当前本地恢复

旧 PostgreSQL 数据不会自动成为当前权威状态。需要保留旧 Session 时，使用当前本地状态迁移命令：

```powershell
learn-agent-core migrate-local-state `
  --workspace D:\Desktop_logo\github\myprojects\learn_langchain `
  --keep-session default
```

默认只执行 dry-run。正式迁移增加 `--apply`；需要删除其他 PostgreSQL 数据时再显式增加
`--prune-source`。完整流程见 [`/docs/operations/local-state-migration.md`](/docs/operations/local-state-migration.md)。

正式迁移要求 daemon 已停止，并在事务前创建完整 `pg_dump`：

1. 优先使用本机 `pg_dump`。
2. 不可用时通过 PostgreSQL Docker 容器执行。
3. 备份失败或为空时拒绝迁移。
4. 数据复制、校验和旧表删除位于同一事务。
5. 任意校验失败都会回滚。

本次真实迁移结果：

| 数据 | 迁移前 | 保留 |
|---|---:|---:|
| Sessions | 3 | 1 |
| Messages | 533 | 503 |
| Memories | 7 | 7 |
| Events | 1735 | 1611 |

完整备份位于用户级 backups 目录，可使用 `pg_restore` 恢复。
