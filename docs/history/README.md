# 历史文档索引

> 文档状态：Current
> 权威范围：历史 Review、迁移设计和已完成整改的导航
> 维护触发：新增、移动、归档或删除历史文档

## 本文负责

- 组织已经完成或失效的设计、Review 和整改记录。
- 指向每份历史材料对应的当前权威来源。
- 防止历史实现被误认为当前行为。

## 本文不负责

- 不定义当前架构、API、配置或运维行为。
- 不登记未来计划；未来计划属于 Product 或明确标记为 Draft 的 Development 文档。

## 历史材料

| 文档 | 历史主题 | 当前权威来源 |
|---|---|---|
| [PR #3 加固记录](/docs/history/pr-3-review-hardening.md) | 双进程重构后的可靠性整改 | Architecture、API、Quality |
| [PR #3 Review 回复](/docs/history/pr-3-review-response.md) | 对外 Review 回应 | Architecture、API、Quality |
| [TUI 实现修复记录](/docs/history/tui-implementation-fixes.md) | TUI 流式与界面问题修复 | [TUI 架构](/docs/architecture/tui-architecture.md) |
| [Workspace 隔离 Review](/docs/history/workspace-isolation-review.md) | 隔离实现审查 | [Workspace 隔离决策](/docs/decisions/workspace-isolation-and-migration.md) |
| [Workspace 隔离重构记录](/docs/history/workspace-isolation-refactor-notes.md) | 全局 Graph、身份传播和跨目录问题 | [Workspace 隔离决策](/docs/decisions/workspace-isolation-and-migration.md) |
| [Workspace PostgreSQL 迁移历史设计](/docs/history/workspace-postgres-migration-design.md) | 旧 PostgreSQL 数据归属迁移 | [本地状态架构](/docs/architecture/database-state-and-consistency.md) |

历史文档可以保留旧类名、旧路径和一次性数据，但开头必须明确 `Historical` 状态及当前权威来源。