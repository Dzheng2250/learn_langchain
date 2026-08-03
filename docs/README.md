# 文档中心

> 文档状态：Current
> 权威范围：项目文档导航、分类和维护入口
> 维护触发：新增、移动、废弃或替换文档

文档按读者要解决的问题分类。若多篇文档描述同一主题，以
[文档登记表](/docs/governance/document-register.md)和
[文档治理规范](/docs/governance/documentation-management.md)确定权威来源。

## 快速入口

| 目标 | 首先阅读 |
|---|---|
| 了解项目目标、能力边界和已知限制 | [项目概述](/docs/product/project-overview.md) |
| 理解整个后端系统如何协作 | [系统架构总览](/docs/architecture/system-overview.md) |
| 开发 CLI、TUI 或其他前端 | [前端开发完整接入指南](/docs/api/frontend-integration-guide.md) |
| 查询公开 RPC、事件和错误 | [RPC 参考](/docs/api/rpc-reference.md)、[流式事件](/docs/api/streaming-events.md)、[错误参考](/docs/api/error-reference.md) |
| 扩展 Tool、Provider、RPC 或 Sink | [Core 平台扩展指南](/docs/development/platform-extension.md) |
| 部署、运行、排障和恢复 | [部署指南](/docs/operations/deployment.md)、[运维 Runbook](/docs/operations/runbook.md) |
| 开发、测试和提交变更 | [开发指南](/docs/development/development-guide.md)、[贡献指南](/CONTRIBUTING.md) |

## 文档分类

### `product/`：项目目标、需求与计划

定义系统要解决什么问题、当前实现了什么，以及哪些能力尚未实现。

- [产品文档索引](/docs/product/README.md)
- [项目概述](/docs/product/project-overview.md)
- [功能需求与实现状态](/docs/product/functional-requirements.md)
- [路线图与已知限制](/docs/product/roadmap-and-known-limitations.md)

### `architecture/`：当前内部实现

解释当前代码如何工作、组件职责、数据流和一致性机制。Architecture 不替代外部 API 契约。

- [架构文档索引](/docs/architecture/README.md)
- [系统架构总览](/docs/architecture/system-overview.md)
- [Core 架构](/docs/architecture/core-architecture.md)
- [CLI 架构](/docs/architecture/cli-architecture.md)
- [TUI 架构](/docs/architecture/tui-architecture.md)
- [Agent 执行架构](/docs/architecture/agent-execution-architecture.md)
- [Agent 执行函数级调用链](/docs/architecture/agent-execution-call-chain.md)
- [面向接口的 Core 设计](/docs/architecture/interface-driven-core.md)
- [本地数据库与一致性](/docs/architecture/database-state-and-consistency.md)
- [安全模型](/docs/architecture/security-model.md)
- [记忆管理](/docs/architecture/memory-management.md)
- [私有任务规划](/docs/architecture/private-task-planning.md)
- [Telemetry Event](/docs/architecture/event-system.md)
- [系统 Trace](/docs/architecture/system-tracing.md)

其他专项架构文档仍位于该目录，并由[文档登记表](/docs/governance/document-register.md)管理。

### `api/`：外部接口契约

面向 CLI、TUI、GUI 和扩展开发者，说明可以发送什么、会收到什么以及失败后如何处理。

- [API 与通信文档索引](/docs/api/README.md)
- [CLI 命令参考](/docs/api/cli-reference.md)
- [IPC 协议](/docs/api/ipc-protocol.md)
- [RPC 方法参考](/docs/api/rpc-reference.md)
- [流式事件参考](/docs/api/streaming-events.md)
- [错误与恢复参考](/docs/api/error-reference.md)
- [TUI 使用与命令参考](/docs/api/tui-reference.md)
- [前端开发完整接入指南](/docs/api/frontend-integration-guide.md)
- [前端接入指南](/docs/api/tui-client-guide.md)
- [协议兼容性](/docs/api/protocol-compatibility.md)
- [Core 平台扩展指南](/docs/development/platform-extension.md)

### `development/`：开发和变更流程

- [开发文档索引](/docs/development/README.md)
- [开发指南](/docs/development/development-guide.md)
- [变更管理清单](/docs/development/change-management.md)
- [发布流程](/docs/development/release-process.md)
- [内部端口与 Adapter 扩展指南](/docs/development/internal-adapter-extension.md)
- [接口化重构技术债务](/docs/development/interface-refactor-backlog.md)

### `operations/`：部署与运维

- [运维文档索引](/docs/operations/README.md)
- [部署指南](/docs/operations/deployment.md)
- [日常运维 Runbook](/docs/operations/runbook.md)
- [备份与恢复](/docs/operations/backup-and-restore.md)
- [升级与回滚](/docs/operations/upgrade-and-rollback.md)
- [PostgreSQL 到本地状态迁移](/docs/operations/local-state-migration.md)

### `quality/`：质量目标与测试

- [质量与测试文档索引](/docs/quality/README.md)
- [测试结构与运行指南](/docs/quality/testing-guide.md)
- [非功能需求](/docs/quality/non-functional-requirements.md)
- [非功能测试](/docs/quality/non-functional-testing.md)
- [本地优先状态测试](/docs/quality/local-first-testing.md)

### `reference/`：稳定事实清单

- [参考文档索引](/docs/reference/README.md)
- [配置参数参考](/docs/reference/configuration-reference.md)
- [本地状态数据库 Schema 参考](/docs/reference/local-state-schema.md)

### `decisions/`：设计决策与取舍

解释为什么选择当前方案。若实现已经变化，以当前 Architecture/API 为准，并更新 Decision 状态。

- [设计决策文档索引](/docs/decisions/README.md)
- [本地优先状态设计](/docs/decisions/local-first-rationale-and-review.md)
- [CLI / Core 双进程与 JSON-RPC](/docs/decisions/cli-core-json-rpc.md)
- [Workspace 隔离设计决策](/docs/decisions/workspace-isolation-and-migration.md)
- [配置、领域常量与 Prompt 边界](/docs/decisions/configuration-and-domain-constants.md)
- [私有任务规划设计决策](/docs/decisions/private-task-planning.md)

### `governance/`：文档治理

- [文档治理索引](/docs/governance/README.md)
- [文档治理规范](/docs/governance/documentation-management.md)
- [文档登记表](/docs/governance/document-register.md)
- [文档模板](/docs/governance/document-template.md)
- [设计决策记录模板](/docs/governance/decision-record-template.md)

### `history/`：历史 Review 与已完成整改

- [历史文档索引](/docs/history/README.md)

仅用于追溯演进过程，不作为当前行为依据。学习笔记、展示文档、`todo` 和 `.agent_runtime/`
同样不是当前实现的权威来源。

## 维护要求

本文只负责导航，不定义文档权威优先级或冲突处理规则。新增、移动或废弃文档时必须同步更新
[文档登记表](/docs/governance/document-register.md)；完整权威关系、同步矩阵和冲突处理流程见
[文档治理规范](/docs/governance/documentation-management.md)。
