# 开发文档索引

> 文档状态：Current
> 权威范围：开发流程、变更管理和发布流程文档入口
> 维护触发：新增、移动、废弃或重命名开发流程文档

本文是 `docs/development/` 的目录级入口。它面向项目维护者和贡献者，说明修改代码、提交变更和发布版本时应该阅读哪些文档。

## 本文负责

- 组织开发流程相关文档。
- 说明开发、变更检查和发布流程的职责边界。
- 防止开发流程说明散落在架构、运维或历史文档中。

## 本文不负责

- 不定义 API 契约；API 变更见 `/docs/api/`。
- 不解释内部架构；架构设计见 `/docs/architecture/`。
- 不定义部署和恢复步骤；运维流程见 `/docs/operations/`。
- 不记录历史 review 过程；历史材料见 `/docs/history/`。

## 文档分组

| 文档 | 负责内容 |
|---|---|
| [开发指南](/docs/development/development-guide.md) | 本地开发环境、常用命令、代码组织和提交前检查 |
| [变更管理清单](/docs/development/change-management.md) | 修改代码时必须同步检查哪些文档、测试、配置和兼容边界 |
| [发布流程](/docs/development/release-process.md) | 维护者发布版本时的检查、标记和回滚准备 |
| [Core 平台扩展指南](/docs/development/platform-extension.md) | Tool、Skill、Provider、RPC、Telemetry 和维护任务的扩展入口 |
| [内部端口与 Adapter 扩展指南](/docs/development/internal-adapter-extension.md) | 新增内部 Port、Adapter、后端和契约测试 |
| [Agent Runtime 扩展指南](/docs/development/agent-runtime-extension.md) | 新增模型用途、工具、运行限制和事件消费者 |
| [新增工具指南](/docs/development/tool-extension-guide.md) | 添加新 LangChain tool 的端到端步骤、audience/risk 决策表、观测层边界与反模式清单 |
| [接口化重构技术债务](/docs/development/interface-refactor-backlog.md) | 尚未完成的接口拆分计划，状态为 Draft |

## 推荐阅读顺序

| 目标 | 阅读顺序 |
|---|---|
| 第一次参与开发 | [开发指南](/docs/development/development-guide.md) -> [测试结构与运行指南](/docs/quality/testing-guide.md) |
| 修改 RPC、CLI、配置或状态库 | [变更管理清单](/docs/development/change-management.md) -> 对应 API / Architecture / Reference 文档 |
| 新增内部存储或 Adapter | [内部端口与 Adapter 扩展指南](/docs/development/internal-adapter-extension.md) -> [面向接口的 Core 设计](/docs/architecture/interface-driven-core.md) |
| 准备发布 | [发布流程](/docs/development/release-process.md) -> [升级与回滚](/docs/operations/upgrade-and-rollback.md) |

## 写作约束

开发文档只描述维护流程和工程约束。若需要解释某个设计为什么存在，应链接到 `/docs/decisions/`；
若需要描述当前代码结构，应链接到 `/docs/architecture/`。
