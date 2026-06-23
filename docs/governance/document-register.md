# 文档登记表

> 文档状态：Current
> 权威范围：文档权威来源、状态和重叠关系
> 维护触发：新增、移动、废弃或替换文档

## 本文负责

- 核心权威来源、特殊状态、重叠归属、文档债务和替代关系。

## 本文不负责

- 不作为所有文件的手工目录；导航见文档中心和目录索引。
- 不定义业务实现。


本文不是全部文档的手工文件清单。`docs/README.md` 负责读者导航，本登记表只负责记录核心权威来源、
特殊状态、已知重叠和文档债务。新增普通专项文档必须更新导航；新增或替换权威来源还必须更新本登记表。

## 1. 核心权威文档

| 主题 | 权威文档 |
|---|---|
| 产品文档入口 | `/docs/product/README.md` |
| 项目目标与边界 | `/docs/product/project-overview.md` |
| 功能需求与实现状态 | `/docs/product/functional-requirements.md` |
| 已知限制与路线图 | `/docs/product/roadmap-and-known-limitations.md` |
| 架构文档入口与模块分组 | `/docs/architecture/README.md` |
| 系统整体结构 | `/docs/architecture/system-overview.md` |
| CLI 内部架构 | `/docs/architecture/cli-architecture.md` |
| TUI 内部架构 | `/docs/architecture/tui-architecture.md` |
| TUI 用户命令与能力 | `/docs/api/tui-reference.md` |
| Agent 执行架构 | `/docs/architecture/agent-execution-architecture.md` |
| Agent Runtime 扩展 | `/docs/development/agent-runtime-extension.md` |
| Agent 函数级调用链 | `/docs/architecture/agent-execution-call-chain.md` |
| Core 内部接口与 IoC 边界 | `/docs/architecture/interface-driven-core.md` |
| 本地状态与数据库一致性 | `/docs/architecture/database-state-and-consistency.md` |
| 本地状态 Schema 事实参考 | `/docs/reference/local-state-schema.md` |
| 私有任务规划 | `/docs/architecture/private-task-planning.md` |
| API 文档入口与通信边界 | `/docs/api/README.md` |
| 外部 RPC | `/docs/api/rpc-reference.md` |
| 流式事件 | `/docs/api/streaming-events.md` |
| 配置参数 | `/docs/reference/configuration-reference.md` |
| 部署 | `/docs/operations/deployment.md` |
| 运维文档入口 | `/docs/operations/README.md` |
| 日常运维 | `/docs/operations/runbook.md` |
| 备份恢复 | `/docs/operations/backup-and-restore.md` |
| 升级回滚 | `/docs/operations/upgrade-and-rollback.md` |
| 开发文档入口 | `/docs/development/README.md` |
| 开发流程 | `/docs/development/development-guide.md` |
| 内部 Port / Adapter 扩展 | `/docs/development/internal-adapter-extension.md` |
| 变更检查 | `/docs/development/change-management.md` |
| Core 平台扩展入口 | `/docs/development/platform-extension.md` |
| 质量文档入口 | `/docs/quality/README.md` |
| 测试结构 | `/docs/quality/testing-guide.md` |
| 参考文档入口 | `/docs/reference/README.md` |
| 文档管理 | `/docs/governance/documentation-management.md` |
| 文档治理入口 | `/docs/governance/README.md` |
| 历史文档入口 | `/docs/history/README.md` |
| 文档模板 | `/docs/governance/document-template.md` |
| 设计决策模板 | `/docs/governance/decision-record-template.md` |
| 设计决策入口与状态说明 | `/docs/decisions/README.md` |

## 2. 当前专项文档

`docs/architecture/` 下的专项文档均为当前实现说明：

- CLI、Core、Agent 执行。
- 本地状态、最终提交、可恢复执行。
- 记忆、私有任务规划、Provider 错误、Event 和 Trace。

专项文档允许包含更详细的函数、类和数据流，但不得重新定义外部协议或产品需求。

专项文档还必须遵守模块职责边界：

- `core-architecture.md` 只负责组合根、Transport、Handler 装配和进程生命周期。
- `agent-execution-architecture.md` 只负责 Agent turn、slice、tool、模型调用、预算和暂停恢复。
- `database-state-and-consistency.md` 只负责状态库、事务、一致性、Outbox、CAS 和 checkpoint 协调。
- `interface-driven-core.md` 只负责 ports、adapters、IoC、DI 和可替换实现边界。
- Event、Trace、Memory、Task、Provider Error 等专项文档只解释自己的模块，不复制其他模块的权威规则。

如果一篇架构文档需要引用其他模块，应使用“摘要 + 链接”，不能复制另一篇文档的字段清单、表结构或完整流程。

## 3. 当前 API 契约文档

`docs/api/` 下的 Current 文档共同组成外部接口契约：

- `README.md`：API 文档入口、阅读顺序和通信边界。
- `cli-reference.md`：CLI 与 Core 管理命令。
- `ipc-protocol.md`：TCP、NDJSON、JSON-RPC 和鉴权。
- `rpc-reference.md`：公开 RPC 方法。
- `streaming-events.md`：服务端流式通知。
- `error-reference.md`：错误类别与恢复策略。
- `tui-reference.md`：内置 TUI 的命令、快捷键和用户可见能力。
- `tui-client-guide.md`：第三方前端接入职责。
- `protocol-compatibility.md`：兼容与不兼容变更规则。

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

`docs/decisions/` 记录重要方案取舍。目录入口是
[`/docs/decisions/README.md`](/docs/decisions/README.md)。

Decision 文档解释为什么采用当前方案，但部分章节包含历史演进内容。若 Decisions 与 Architecture/API
冲突，以当前代码、测试、Schema 和 Current Architecture/API 为准，并更新 Decision 的状态说明。

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
- CLI/Core/Agent 架构均涉及请求调用链：系统总览展示全局关系，Agent 架构保存执行模型，
  `agent-execution-call-chain.md` 单独保存详细函数级流程。
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
| `docs/decisions/*` | Decision 解释取舍但不代表当前实现 | 已统一 Current Decision 状态和职责边界；冲突时以 Architecture/API 为准 |
| `agent-execution-architecture.md` | 曾同时承担函数调用链、事件系统图、扩展教程和未实现清单 | 函数流程拆到 Call Chain；事件改为链接；扩展迁到 Development；能力状态归 Product |
| `agent-execution-call-chain.md` | 接口拆分后仍引用已移除的 Service 私有方法，worker slot 图示顺序也与实现不符 | 已按 AsyncRunner、Worker、SyncRunner、RequestStream、ExecutionLoop 和 SliceService 的真实调用关系更新 |
| `api/extension-guide.md` | 内部 Core 扩展流程误放在外部 API 契约目录，并存在乱码与重复内容 | 当前指南迁到 `development/platform-extension.md`；旧路径仅保留 Redirect |
| `cli-architecture.md` | 曾复制设计优缺点、命令、RPC、配置和安全清单，部分内容已经过时 | 已收敛为 CLI 内部架构；取舍归 Decision，功能清单归 API/Reference |
| `tui-architecture.md` | 曾混入历史修复、TUI 命令、能力清单和数据库迁移说明 | 当前架构留在 Architecture；用户接口迁到 `api/tui-reference.md`；修复过程迁到 History |
| `database-state-and-consistency.md` | 曾同时维护一致性原理和完整 Schema 事实，更新触发条件过多 | 一致性留在 Architecture；表关系、约束和索引迁到 `reference/local-state-schema.md` |
| `core-architecture.md` | 曾保留重构历史、完整目录树、设计原则、优缺点和功能清单，容易与 Interface/API 重复 | 当前只保留组合根、生命周期、Handler、Bus 和 Transport；其余改为权威链接 |
| `memory-management.md` | 曾复制数据库字段、事件清单、一致性流程和独立路线图，并仍以兼容 facade 为主要入口 | 当前只保留记忆领域流程；Schema/Event/一致性/路线图改为链接，代码入口改为 Ports/Adapters |
| `interface-driven-core.md` | 曾同时维护当前架构、扩展教程和未来重构清单 | 当前架构保留在 Architecture；扩展教程和 Draft Backlog 迁到 Development |
| `cli-core-json-rpc.md` | 曾复制当前函数调用链、RPC 方法和功能限制，导致 Decision 与 Architecture/API/Product 形成多份事实 | Decision 只保留进程、协议和安全边界取舍；实现调用链、接口清单与限制改为链接各自权威文档 |
| `local-first-rationale-and-review.md` | 曾复制具体类、实时关键路径、本机路径和后续任务，Decision 容易随实现漂移 | 只保留本地优先、最小提交、异步维护和跨库恢复的取舍；实现、指标、路径和路线图改为权威链接 |
| `workspace-isolation-and-migration.md` | 曾混入具体 Repository/Runtime 类、用户路径、实现难点和当前功能清单 | Decision 只保留隔离取舍；实现难点迁到 History，路径和功能边界改为权威链接 |
| `configuration-and-domain-constants.md` | 曾复制具体文件、Schema 迁移细节和下一批重构清单 | 只保留配置/枚举/Prompt 分类决策；当前事实与技术债务改为 Reference、Architecture 和 Draft 链接 |

### 当前 Draft 计划

- [接口化重构技术债务](/docs/development/interface-refactor-backlog.md)记录尚未完成的内部拆分，
  不得作为已实现能力引用。

## 9. 过时内容处理

发现过时文档时，不直接删除仍可能被引用的内容：

1. 标记为 `Deprecated` 或移动至 `history/`。
2. 在开头链接替代文档。
3. 更新文档中心和所有入口链接。
4. 通过文档契约测试确认没有失效链接。


## Provider 错误恢复权威补充

| 主题 | 权威文档 |
|---|---|
| 通用 LLM 错误恢复、重试和草稿失效语义 | `/docs/architecture/provider-error-recovery.md` |
