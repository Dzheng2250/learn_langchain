# 文档导航与版本状态

项目文档分为三类。阅读时应优先参考 Current 文档；Historical 文档用于理解演进过程，不应直接
作为当前运行行为的依据。

## Current：当前规范

| 主题 | 文档 |
|---|---|
| 快速开始与项目入口 | [`../README.md`](../README.md) |
| 全部配置参数、默认值与风险 | [`configuration-reference.md`](configuration-reference.md) |
| Core 组件与生命周期 | [`core-architecture.md`](core-architecture.md) |
| CLI、daemon 与 JSON-RPC | [`cli-architecture.md`](cli-architecture.md) |
| Agent 调用链与数据流 | [`agent-execution-architecture.md`](agent-execution-architecture.md) |
| SQLite 数据模型、事务与一致性 | [`database-state-and-consistency.md`](database-state-and-consistency.md) |
| 最终响应与后台维护 | [`response-finalization-and-checkpoint-consistency.md`](response-finalization-and-checkpoint-consistency.md) |
| 可恢复执行与预算 | [`resumable-execution.md`](resumable-execution.md) |
| 记忆管理 | [`memory-management.md`](memory-management.md) |
| Telemetry Event 系统 | [`event-system.md`](event-system.md) |
| 系统级 Trace 时间线 | [`system-tracing.md`](system-tracing.md) |
| 部署 | [`deployment.md`](deployment.md) |

## Current Design Note：当前设计说明

这类文档解释为什么采用某种设计，但不替代配置参考或运行规范：

- [`configuration-and-domain-constants.md`](configuration-and-domain-constants.md)
- [`local-first-rationale-and-review.md`](local-first-rationale-and-review.md)
- [`non-functional-requirements.md`](non-functional-requirements.md)
- [`non-functional-testing.md`](non-functional-testing.md)
- [`local-first-testing.md`](local-first-testing.md)

## Migration / Historical：迁移与历史记录

- [`local-state-migration.md`](local-state-migration.md)：当前仍可执行的 PostgreSQL 到本地状态迁移手册。
- [`workspace-isolation-and-migration.md`](workspace-isolation-and-migration.md)：Workspace 原则仍有效，
  其中 PostgreSQL 权威存储部分属于历史方案。
- [`pr-3-review-hardening.md`](pr-3-review-hardening.md)：历史 Review 整改记录。
- [`pr-3-review-response.md`](pr-3-review-response.md)：历史 Review 回复。
- [`workspace-isolation-review.md`](workspace-isolation-review.md)：Workspace 隔离首次实施后的历史审查。

## 文档维护规则

1. 当前行为变化时，先更新 Current 文档，再更新设计说明。
2. 已被替代的方案不要静默删除；标记为 Historical，并链接到当前规范。
3. 配置默认值变化时，同时更新：
   - `src/config/`
   - `.env.example`
   - [`configuration-reference.md`](configuration-reference.md)
4. 新增 CLI 命令或 RPC 方法时，同时更新 CLI 架构和相关流程文档。
5. 文档中的代码路径、命令和链接必须在提交前执行一致性检查。
