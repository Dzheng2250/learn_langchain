# 文档登记表

> 文档状态：Current
> 权威范围：文档权威来源、状态和重叠关系
> 维护触发：新增、移动、废弃或替换文档

本文不是全部文档的手工文件清单。`docs/README.md` 负责读者导航，本登记表只负责记录核心权威来源、
特殊状态、已知重叠和文档债务。新增普通专项文档必须更新导航；新增或替换权威来源还必须更新本登记表。

## 1. 核心权威文档

| 主题 | 权威文档 |
|---|---|
| 项目目标与边界 | `/docs/product/project-overview.md` |
| 功能需求与实现状态 | `/docs/product/functional-requirements.md` |
| 已知限制与路线图 | `/docs/product/roadmap-and-known-limitations.md` |
| 系统整体结构 | `/docs/architecture/system-overview.md` |
| CLI 内部架构 | `/docs/architecture/cli-architecture.md` |
| TUI 内部架构 | `/docs/architecture/tui-architecture.md` |
| Agent 执行调用链 | `/docs/architecture/agent-execution-architecture.md` |
| 本地状态与数据库一致性 | `/docs/architecture/database-state-and-consistency.md` |
| 私有任务规划 | `/docs/architecture/private-task-planning.md` |
| 外部 RPC | `/docs/api/rpc-reference.md` |
| 流式事件 | `/docs/api/streaming-events.md` |
| 配置参数 | `/docs/reference/configuration-reference.md` |
| 部署 | `/docs/operations/deployment.md` |
| 日常运维 | `/docs/operations/runbook.md` |
| 备份恢复 | `/docs/operations/backup-and-restore.md` |
| 升级回滚 | `/docs/operations/upgrade-and-rollback.md` |
| 开发流程 | `/docs/development/development-guide.md` |
| 变更检查 | `/docs/development/change-management.md` |
| 扩展接口 | `/docs/api/extension-guide.md` |
| 测试结构 | `/docs/quality/testing-guide.md` |
| 文档管理 | `/docs/governance/documentation-management.md` |
| 文档模板 | `/docs/governance/document-template.md` |
| 设计决策模板 | `/docs/governance/decision-record-template.md` |

## 2. 当前专项文档

`docs/architecture/` 下的专项文档均为当前实现说明：

- CLI、Core、Agent 执行。
- 本地状态、最终提交、可恢复执行。
- 记忆、私有任务规划、Provider 错误、Event 和 Trace。

专项文档允许包含更详细的函数、类和数据流，但不得重新定义外部协议或产品需求。

## 3. 当前 API 契约文档

`docs/api/` 下的 Current 文档共同组成外部接口契约：

- `cli-reference.md`：CLI 与 Core 管理命令。
- `ipc-protocol.md`：TCP、NDJSON、JSON-RPC 和鉴权。
- `rpc-reference.md`：公开 RPC 方法。
- `streaming-events.md`：服务端流式通知。
- `error-reference.md`：错误类别与恢复策略。
- `tui-client-guide.md`：第三方前端接入职责。
- `protocol-compatibility.md`：兼容与不兼容变更规则。
- `extension-guide.md`：Tool、Provider、RPC 和 Sink 扩展边界。

API 文档不得声明未实现的外部能力。新增、移动或废弃 API 契约文档时，必须同步更新本节、
`docs/README.md` 和文档契约测试。

## 4. 文档覆盖状态

| 文档领域 | 当前覆盖 | 主要权威入口 | 尚未闭环的部分 |
|---|---|---|---|
| 项目概述与需求 | 已建立统一基线 | `product/` | 正式版本目标和用户验收案例仍需随产品演进补充 |
| 架构与设计 | 覆盖核心进程、Agent、状态、一致性、安全和观测 | `architecture/`、`decisions/` | 部分旧 Decision 尚未补充状态与替代关系 |
| 接口与通信 | 覆盖 CLI、IPC、RPC、事件、错误和兼容规则 | `api/` | 无协议版本协商，部分管理 RPC 尚未实现 |
| 开发与测试规范 | 覆盖开发、扩展、变更、贡献和测试分类 | `development/`、`quality/`、`CONTRIBUTING.md` | 无 formatter、linter、类型检查和测试 CI |
| 部署与运维 | 覆盖部署、Runbook、备份恢复、升级回滚和迁移 | `operations/` | 无自动备份、自动恢复演练和 OS 服务管理 |

“尚未闭环”表示当前系统或工程流程的真实缺口，不应通过文档描述假装已经实现。详细状态统一登记在
[路线图与已知限制](/docs/product/roadmap-and-known-limitations.md)。

## 5. 设计决策文档

`docs/decisions/` 记录重要方案取舍。它们解释为什么采用当前方案，但部分章节包含历史演进内容。
若 Decisions 与 Architecture 冲突，以当前代码和 Architecture 为准，并更新 Decision 的状态说明。

## 6. 历史和低权威内容

| 内容 | 状态 | 使用规则 |
|---|---|---|
| `docs/history/*` | Historical | 仅用于追溯 Review 和整改 |
| `学习文档.md` | Historical / Learning Notes | 可能描述旧实现，不作为当前契约 |
| `面试答辩文档.md` | Presentation | 用于展示，不作为实现依据 |
| `.agent_runtime/*` | Local Runtime | 本地运行数据，不进入项目文档体系 |
| `todo` | Personal Planning | 个人思考和草稿，不作为需求基线 |

## 7. 已知重叠实例与收敛归属

本节只登记当前仓库中已经识别的具体重叠及其权威归属。通用冲突处理流程只由
[文档治理规范](/docs/governance/documentation-management.md)定义。

- `README.md` 与部署指南均包含快速启动：README 只保留最短路径，部署指南保留完整说明。
- CLI/Core/Agent 架构均涉及请求调用链：系统总览展示全局关系，Agent 架构保存详细函数级流程。
- 本地状态、最终提交和可恢复执行均涉及 checkpoint：数据库架构定义事实与一致性，专项文档解释具体流程。
- 多篇文档包含“当前不支持”：统一状态必须同步登记到产品路线图。
- 发布维护流程与升级操作曾存在重复：维护者发布门禁由 `development/release-process.md` 负责，
  用户升级和回滚步骤由 `operations/upgrade-and-rollback.md` 负责。

## 8. 已识别的文档债务

| 内容 | 问题 | 当前处理 |
|---|---|---|
| `docs/operations/local-state-migration.md` | 同时包含可复用迁移流程和 2026-06-13 历史执行结果 | 保留为专项迁移说明；历史数量不得复制到其他 Current 文档 |
| `学习文档.md` | 跨越多个旧版本，部分代码和路径已经过时 | 标记为低权威学习记录；Current 文档不得引用其行为结论 |
| `面试答辩文档.md` | 为展示而简化实现 | 标记为 Presentation；不得作为接口或运维依据 |
| `docs/decisions/*` | 部分决策文件含当前实现细节 | 冲突时以 Architecture/API 为准，后续逐篇补充状态和替代关系 |

## 9. 过时内容处理

发现过时文档时，不直接删除仍可能被引用的内容：

1. 标记为 `Deprecated` 或移动至 `history/`。
2. 在开头链接替代文档。
3. 更新文档中心和所有入口链接。
4. 通过文档契约测试确认没有失效链接。
