# 设计决策文档索引

> 文档状态：Current
> 权威范围：设计决策文档入口、状态解释和与当前架构文档的关系
> 维护触发：新增、移动、废弃或重命名设计决策文档

本文是 `docs/decisions/` 的目录级入口。Decision 文档解释“为什么选择这个方案”，不替代
`docs/architecture/` 和 `docs/api/` 中的当前实现与外部契约。

## 本文负责

- 组织当前设计决策文档。
- 说明 Decision 与 Architecture/API 的权威关系。
- 标明哪些文档包含历史演进内容。
- 给出新增设计决策文档的写作要求。

## 本文不负责

- 不定义当前代码调用链；这些属于 `/docs/architecture/`。
- 不定义外部 RPC、事件、CLI 命令或兼容规则；这些属于 `/docs/api/`。
- 不保存 PR review 过程；这些属于 `/docs/history/`。

## 权威关系

当 Decision 与当前实现文档发生冲突时：

1. 先以代码、测试和 Schema 为事实来源。
2. 当前行为以 `architecture/` 和 `api/` 的 Current 文档为准。
3. Decision 文档应更新状态或补充“当前实现已变化”说明。

Decision 的核心价值是记录取舍，而不是复制当前实现细节。比如：

- “为什么选择 TCP + NDJSON + JSON-RPC”属于 Decision。
- “`agent.chat` 参数字段是什么”属于 API。
- “CoreApp 如何装配 Router 和 Transport”属于 Architecture。

## 当前决策清单

| 文档 | 决策主题 | 当前用途 |
|---|---|---|
| [CLI / Core 双进程与 JSON-RPC](/docs/decisions/cli-core-json-rpc.md) | 为什么拆分 CLI 与 Core daemon，为什么采用本地 TCP + NDJSON + JSON-RPC | 解释进程与通信方案取舍 |
| [本地优先状态设计](/docs/decisions/local-first-rationale-and-review.md) | 为什么从 PostgreSQL 关键路径迁移到本地优先状态与后台维护 | 解释响应延迟、一致性和恢复策略的来源 |
| [Workspace 隔离与迁移](/docs/decisions/workspace-isolation-and-migration.md) | 为什么选择用户级 daemon + Workspace 隔离 | 解释 Session、记忆和工具为何绑定 Workspace |
| [配置、领域常量与 Prompt 边界](/docs/decisions/configuration-and-domain-constants.md) | 为什么拆分运行配置、领域枚举和 Prompt | 解释配置治理和代码边界 |
| [私有任务规划设计决策](/docs/decisions/private-task-planning.md) | 为什么任务规划只作为 goal 模式下父 Agent 私有工具 | 解释任务系统定位、存储和用户边界 |

## 阅读建议

| 问题 | 首先阅读 |
|---|---|
| 为什么项目是双进程，而不是 CLI 直接跑 Agent | [CLI / Core 双进程与 JSON-RPC](/docs/decisions/cli-core-json-rpc.md) |
| 为什么普通对话不再依赖 PostgreSQL | [本地优先状态设计](/docs/decisions/local-first-rationale-and-review.md) |
| 为什么不同项目的 Session 和记忆隔离 | [Workspace 隔离与迁移](/docs/decisions/workspace-isolation-and-migration.md) |
| 为什么配置、枚举、Prompt 分开管理 | [配置、领域常量与 Prompt 边界](/docs/decisions/configuration-and-domain-constants.md) |
| 为什么任务系统不暴露给用户直接 CRUD | [私有任务规划设计决策](/docs/decisions/private-task-planning.md) |

## 写作约束

新增 Decision 文档时：

1. 使用 [设计决策记录模板](/docs/governance/decision-record-template.md)。
2. 明确决策状态，例如 `Current Decision`、`Superseded Decision` 或 `Historical Decision`。
3. 写清当时要解决的问题、考虑过的替代方案、最终选择和代价。
4. 不复制 API 字段清单、数据库表结构或函数级调用链；这些应链接到当前权威文档。
5. 如果后续实现变化，更新 Decision 状态，并链接替代文档。
