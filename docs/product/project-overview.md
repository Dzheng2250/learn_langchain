# 项目概述

> 文档状态：Current
> 权威范围：项目目标、目标用户、系统边界
> 维护触发：项目定位、核心用户场景或产品边界变化

## 本文负责

- 项目定位、目标用户、核心原则和系统边界。

## 本文不负责

- 不列出全部功能细节。
- 不解释具体架构、API 或部署步骤。


## 1. 项目定位

Learn LangChain Agent 是一个通过 Vibe Coding 与人工审查持续迭代的本地 Coding Agent 学习项目。
项目目标不是复刻完整商业产品，而是构建一个可解释、可测试、可恢复的 Agent 后端系统，用于实践：

- Agent 推理循环与工具编排。
- 长对话上下文、长期记忆与完整历史管理。
- 长任务预算控制、暂停与恢复。
- Workspace 隔离和本地工具安全边界。
- CLI、未来 TUI 与后台 Core daemon 的稳定通信。
- 事件、Trace、错误处理和后台维护等工程能力。

## 2. 目标用户

当前主要用户是项目开发者本人，以及希望研究 Agent 后端架构的开发者。

典型使用场景：

1. 在一个本地 Workspace 中发起持续 Coding Agent 对话。
2. 让 Agent 读取文件、执行受限命令、加载 Skill 或委托子 Agent。
3. 在任务达到单次预算后恢复继续执行，而不是重新开始。
4. 重启 daemon 后继续访问已有 Session、消息、记忆和待恢复任务。
5. 通过 Trace、Telemetry、状态命令和日志排查一次执行。
6. 使用相同 JSON-RPC 协议开发新的 CLI、TUI 或 GUI 前端。

## 3. 核心设计原则

### 本地优先

SQLite `state.db` 是 Session、消息、记忆和 Execution 的权威来源。普通对话不依赖 PostgreSQL。

### Agent 执行与前端解耦

CLI 只负责命令解析、输入和展示。Core daemon 负责模型、工具、状态与恢复。二者通过本地
TCP + NDJSON + JSON-RPC 通信。

### Workspace 严格隔离

Session、记忆、Skill、文件工具和命令工具必须绑定当前 Workspace。不同 Workspace 可以拥有同名
Session，但不能互相读取业务状态。

### 最小提交后立即释放用户

模型回答完成后，Core 只同步提交保证消息不丢失所需的最小业务状态。摘要、记忆提取、Trace、
Telemetry 和 checkpoint 清理不得阻塞普通响应。

### 可恢复而不是无限执行

一次请求只授予有限执行预算。复杂任务达到边界后保存 Execution 与 checkpoint，由用户显式恢复。

### 可观测但不泄漏

Telemetry 和 System Trace 用于诊断，不是业务事实来源；默认只记录脱敏摘要，不保存完整 Prompt、
用户消息、文件内容或工具结果。

## 4. 系统边界

当前系统负责：

- 本地用户级 daemon 生命周期。
- Workspace 与 Session 身份管理。
- Agent、工具、子 Agent、Skill、上下文和记忆。
- 本地持久化、后台维护、Execution 与 checkpoint 恢复。
- JSON-RPC、流式事件、错误分类、Telemetry 和 Trace。

当前系统不负责：

- 多用户远程服务、账户系统、角色权限和公网暴露。
- 自动代码变更审批或完整人工授权界面。
- 跨设备同步、云端 Session 服务或协作编辑。
- 合规审计、计费和强一致 Trace。
- Kubernetes、高可用数据库和自动容灾。

完整功能状态见[功能需求](/docs/product/functional-requirements.md)和
[路线图与已知限制](/docs/product/roadmap-and-known-limitations.md)。

## 5. 成功标准

项目是否成功，不以代码数量衡量，而以以下结果衡量：

- 用户可以稳定完成多轮 Agent 对话，并在重启后恢复状态。
- 长任务达到预算后不会静默丢失进度。
- 工具无法越过 Workspace 的文件边界。
- 普通后台维护不会显著延迟 token 输出和最终响应。
- 新增 Tool、Provider、RPC 或前端时存在明确扩展边界和测试路径。
- 关键设计选择、已知风险和未实现能力在文档中可追踪。
