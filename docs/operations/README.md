# 运维文档索引

> 文档状态：Current
> 权威范围：部署、运行、备份、恢复、升级和迁移文档入口
> 维护触发：部署方式、运行目录、备份恢复、升级回滚或迁移流程变化

本文是 `docs/operations/` 的目录级入口。它面向使用者和维护者，说明如何把系统运行起来，以及出现问题时如何排查和恢复。

## 本文负责

- 组织部署、Runbook、备份恢复、升级回滚和迁移文档。
- 说明每篇运维文档的使用场景。
- 防止运维命令散落到架构、决策或开发文档中。

## 本文不负责

- 不定义内部架构设计；架构见 `/docs/architecture/`。
- 不解释为什么选择某个方案；设计原因见 `/docs/decisions/`。
- 不定义外部 API；接口契约见 `/docs/api/`。

## 文档分组

| 文档 | 负责内容 |
|---|---|
| [部署指南](/docs/operations/deployment.md) | 从零安装、配置 `.env`、启动数据库和 daemon |
| [日常运维 Runbook](/docs/operations/runbook.md) | 日常状态检查、启动停止、排障和恢复入口 |
| [备份与恢复](/docs/operations/backup-and-restore.md) | 本地状态和可选 PostgreSQL 的备份、恢复和验证 |
| [升级与回滚](/docs/operations/upgrade-and-rollback.md) | 版本升级、Schema migration、失败回滚和兼容检查 |
| [PostgreSQL 到本地状态迁移](/docs/operations/local-state-migration.md) | 旧 PostgreSQL 数据迁移到本地 `state.db` 的专项流程 |

## 推荐阅读顺序

| 目标 | 阅读顺序 |
|---|---|
| 第一次部署 | [部署指南](/docs/operations/deployment.md) -> [日常运维 Runbook](/docs/operations/runbook.md) |
| 运行中出问题 | [日常运维 Runbook](/docs/operations/runbook.md) -> [错误与恢复参考](/docs/api/error-reference.md) -> [系统 Trace](/docs/architecture/system-tracing.md) |
| 升级前准备 | [备份与恢复](/docs/operations/backup-and-restore.md) -> [升级与回滚](/docs/operations/upgrade-and-rollback.md) |
| 处理旧 PostgreSQL 数据 | [PostgreSQL 到本地状态迁移](/docs/operations/local-state-migration.md) |

## 写作约束

运维文档必须写清前置条件、执行命令、预期结果和失败后的处理方式。不要把一次性历史数据写进 Current
运维流程；历史快照应放入 `/docs/history/` 或在正文中明确标注为历史记录。
