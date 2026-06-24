# 功能需求

> 文档状态：Current
> 权威范围：当前产品功能基线与验收条件
> 维护触发：新增、删除或改变用户可见能力

## 本文负责

- 用户和外部客户端可感知的功能基线、状态和验收条件。

## 本文不负责

- 不解释内部实现。
- 不记录纯技术债或未来设想。


本文只记录用户或外部客户端可感知的功能需求。内部实现方式以 Architecture 和 Decisions 文档为准。

状态说明：

- `Implemented`：当前代码已实现并有自动测试或明确验证路径。
- `Partial`：已有基础实现，但存在已知能力缺口。
- `Planned`：已确认需要，但当前尚未实现。
- `Out of scope`：当前项目明确不承担。

## 1. Agent 与会话

| ID | 需求 | 状态 | 当前验收依据 |
|---|---|---|---|
| FR-AGENT-001 | 用户可以发起单次或交互式 Agent 对话 | Implemented | `learn-agent chat`、`agent.chat` |
| FR-AGENT-002 | 空输入不得触发模型调用 | Implemented | CLI 忽略空输入 |
| FR-AGENT-003 | 未配置模型密钥时仍可验证 CLI、RPC、Workspace 与本地状态链路 | Implemented | 诊断 Turn 返回 `llm_not_configured` |
| FR-AGENT-004 | 同一 Session 的 Turn 串行，不同 Session 可并行 | Implemented | Session UUID 锁与 Agent worker 上限 |
| FR-AGENT-005 | 用户可以查询、恢复或丢弃待恢复 Execution | Implemented | `session.status/resume/discard` |
| FR-AGENT-006 | 用户可以归档 Session，或显式硬删除 Session 及其本地关联数据 | Implemented | `session.delete` / `learn-agent session delete --hard` |
| FR-AGENT-006 | 用户可以显式启动 goal 模式，让父 Agent 私有拆解复杂目标并跨 resume 延续计划 | Implemented | `learn-agent chat --goal`、`agent.chat.goal_mode`、私有任务表 |
| FR-AGENT-007 | 用户可以列出 Session 和读取完整历史 | Planned | 当前没有对应 RPC/CLI |
| FR-AGENT-008 | 用户可以取消正在执行的 Turn | Planned | 当前断开客户端不会取消 Core 任务 |

## 2. 工具、Skill 与子 Agent

| ID | 需求 | 状态 | 当前验收依据 |
|---|---|---|---|
| FR-TOOL-001 | Agent 可以读取 Workspace 内文件 | Implemented | Workspace 文件工具 |
| FR-TOOL-002 | Agent 可以在容器沙箱中执行受限命令 | Implemented | Docker 命令工具 |
| FR-TOOL-003 | 文件和命令工具不得访问 Workspace 外路径 | Implemented | 路径解析与符号链接逃逸检查 |
| FR-TOOL-004 | Agent 可以按需发现并读取 Workspace Skill | Implemented | `skills/<name>/SKILL.md` |
| FR-TOOL-005 | 父 Agent 可以委托短生命周期子 Agent | Implemented | delegate tool；子 Agent 不能再次委托 |
| FR-TOOL-006 | 高风险工具执行前由用户交互审批 | Planned | 当前只有风险分类和预算，没有审批通道 |
| FR-TOOL-007 | 工具结果过大时使用摘要、截断或 Artifact，避免直接进入上下文 | Partial | 文件/命令工具有限制；未对所有工具统一 Artifact 化 |

## 3. 上下文、历史与记忆

| ID | 需求 | 状态 | 当前验收依据 |
|---|---|---|---|
| FR-CONTEXT-001 | 每轮加载近期消息、已有摘要和当前 Workspace 相关记忆 | Implemented | AgentContextManager 与 LocalStateStore |
| FR-CONTEXT-002 | 完整用户和 AI 消息必须持久化，不因上下文压缩而丢失 | Implemented | `messages` 表 |
| FR-CONTEXT-003 | 超过阈值的历史在后台生成摘要 | Implemented | `context_summary` maintenance job |
| FR-CONTEXT-004 | 显式“记住”请求进入后台记忆提取并暴露 pending 状态 | Implemented | `memory_extract` maintenance job |
| FR-CONTEXT-005 | 不同 Workspace 的长期记忆严格隔离 | Implemented | memory 查询携带 `workspace_id` |
| FR-CONTEXT-006 | 语义向量记忆检索 | Planned | 当前为关键词和有限回退策略 |
| FR-CONTEXT-007 | 用户可以查看、修改或删除长期记忆 | Planned | 当前无公开 RPC |

## 4. 状态、恢复与一致性

| ID | 需求 | 状态 | 当前验收依据 |
|---|---|---|---|
| FR-STATE-001 | daemon 重启后 Session、消息和记忆仍可读取 | Implemented | 本地 `state.db` |
| FR-STATE-002 | Turn 完成前必须原子提交最小业务状态 | Implemented | CompletedTurnCommitter |
| FR-STATE-003 | 达到执行预算时保存可恢复进度 | Implemented | Execution、Slice 与 LangGraph checkpoint |
| FR-STATE-004 | checkpoint 清理失败不得使已完成回答失败 | Implemented | Transactional Outbox + maintenance job |
| FR-STATE-005 | Core 启动时对账 Execution 与 checkpoint 状态 | Implemented | ExecutionRecoveryCoordinator |
| FR-STATE-006 | Session 历史分支、编辑和版本回退 | Partial | Schema 已有 branch 基础；无公开操作接口 |

## 5. 前端、协议与诊断

| ID | 需求 | 状态 | 当前验收依据 |
|---|---|---|---|
| FR-IPC-001 | CLI 与 Core 使用严格验证的本地 JSON-RPC 通信 | Implemented | TCP + NDJSON + Pydantic |
| FR-IPC-002 | Agent token、步骤、错误和完成状态可以流式推送 | Implemented | `agent.event` |
| FR-IPC-003 | 所有特权 RPC 必须验证本地 daemon token | Implemented | RpcRouter 鉴权 |
| FR-IPC-004 | 协议支持客户端与 Core 版本协商 | Planned | 当前无协议版本协商 |
| FR-OBS-001 | 用户可以离线查询跨层 Trace | Implemented | `learn-agent trace` |
| FR-OBS-002 | 模型服务商错误被分类为稳定、可扩展的错误事实 | Implemented | ProviderErrorParserRegistry |
| FR-OBS-003 | Trace 或 Telemetry 写入失败不得影响 Agent 结果 | Implemented | 有界后台队列与失败隔离 |

## 6. 变更规则

新增或改变功能时必须：

1. 更新本文件对应需求状态和验收依据。
2. 更新相关 API、Architecture 或 Decision 文档。
3. 增加测试，或在[路线图与已知限制](/docs/product/roadmap-and-known-limitations.md)记录无法测试的原因。
4. 用户可见协议变化必须遵守[协议兼容策略](/docs/api/protocol-compatibility.md)。
