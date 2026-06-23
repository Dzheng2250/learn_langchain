# 系统架构总览

> 文档状态：Current
> 权威范围：系统级组件、进程、存储和主要数据流
> 维护触发：新增核心进程、权威存储或跨层调用关系

## 本文负责

- 系统级进程、组件、权威存储和端到端主要数据流。
- 为专项架构文档提供首要入口。

## 本文不负责

- 不解释函数级调用链、表字段或 RPC payload。
- 不记录设计取舍历史或部署步骤。


本文是架构文档的首要入口。它只描述全局结构；函数级调用链和专项机制通过链接下钻。

## 1. 系统组成

```mermaid
flowchart LR
    User[用户] --> CLI[learn-agent CLI]
    User --> TUI[learn-agent TUI]
    CLI <-->|TCP + NDJSON + JSON-RPC| Core[learn-agent-core daemon]
    TUI <-->|TCP + NDJSON + JSON-RPC| Core

    Core --> Agent[AgentTurnService]
    Agent --> Runtime[WorkspaceRuntime]
    Runtime --> LLM[Anthropic LLM / legacy provider]
    Runtime --> Tools[Tool Registry]
    Runtime --> Graph[LangGraph]

    Agent --> State[(state.db)]
    Graph --> Checkpoints[(checkpoints.db)]
    Tools --> Workspace[Workspace Files]
    Tools --> Docker[Docker Sandbox]

    Core --> Maintenance[MaintenanceScheduler]
    Maintenance --> State
    Maintenance --> Checkpoints

    Core --> Telemetry[Telemetry Event]
    Core --> Trace[System Trace JSONL]
```

## 2. 进程职责

### CLI / TUI 进程

CLI 和 TUI 是 Core daemon 的前端客户端，共享同一套 IPC 协议。它们只负责用户交互层，不触达业务逻辑。

CLI 负责：

- 命令解析、Workspace 发现和用户输入。
- 读取 daemon token，构造 JSON-RPC 请求。
- 管理 daemon 的启动、停止与状态。
- 流式事件终端渲染。

TUI 负责（基于 Textual 框架）：

- 实时流式 token 展示（每个 chunk 到达即显示）。
- 状态栏展示连接、session 和上下文用量（`ctx: 3K/128K (2%)`）。
- 暂停 execution 检测与恢复（`/resume` / `/discard`）。
- Goal 模式任务规划展示。

二者都不负责：

- 直接调用模型或工具。
- 直接读取或修改 Session 数据库。
- 决定任务恢复、记忆或上下文策略。

### Core daemon 进程

负责：

- 验证、鉴权和路由 JSON-RPC。
- 执行 Agent、工具、子 Agent 和 Skill。
- 管理 Session、上下文、记忆、Execution 和 checkpoint。
- 运行后台维护、Telemetry 和 Trace。

Core daemon 是唯一允许执行 Agent 与工具的进程。

## 3. 核心运行路径

```mermaid
sequenceDiagram
    participant U as User
    participant F as CLI / TUI
    participant R as RpcRouter
    participant A as AgentTurnService
    participant G as LangGraph / Tools
    participant S as state.db
    participant M as Maintenance

    U->>F: 输入消息
    F->>R: agent.chat
    R->>A: 已验证 Workspace + Session + message
    A->>S: 加载 Session、近期上下文和记忆
    A->>G: 执行一个或多个受预算 Slice
    G-->>F: token / step / error 通知
    A->>S: 原子提交完整消息和 Execution 状态
    A-->>F: done + 最终响应
    A->>M: 唤醒后台任务
    M->>S: 摘要、记忆提取或状态写回
```

关键边界：

- JSON-RPC 请求验证成功后才能进入 Agent。
- 同一 Session 通过内部 UUID 锁串行执行。
- 最终响应必须等待最小业务提交，但不等待摘要、记忆和 checkpoint 清理。
- Trace、Telemetry 和 PostgreSQL 可选能力不得成为业务成功条件。

## 4. 状态与存储

| 存储 | 角色 | 是否权威 | 典型内容 |
|---|---|---:|---|
| `state.db` | 本地业务状态 | 是 | Workspace、Session、消息、记忆、Execution、维护任务 |
| `checkpoints.db` | LangGraph 恢复断点 | 辅助 | 未完成 Slice 的图状态 |
| `artifacts/` | 大内容存储 | 辅助 | 去重的大型工具内容 |
| `telemetry/` | 领域观测事件 | 否 | 工具、记忆、上下文等事件 |
| `traces/` | 跨层诊断时间线 | 否 | IPC、Agent、LLM、Tool 调用摘要 |
| PostgreSQL | 可选迁移来源与 Event Sink | 否 | 旧数据、可选事件 |

状态路径和配置见[配置参考](/docs/reference/configuration-reference.md)，事务与恢复机制见
[本地数据库与一致性](/docs/architecture/database-state-and-consistency.md)。

## 5. Agent 执行模型

一次用户请求形成一个 `run_id`。复杂任务可以附着到一个跨请求存在的 `execution_id`，并被拆成多个
受预算限制的 Slice。

```text
Run：一次 chat 或 resume 请求
Execution：一个可跨多次 Run 恢复的任务
Slice：一次受 LangGraph 步数限制的执行片段
```

Agent 由 LangGraph 控制模型与工具节点循环；应用层负责并发、状态、恢复和最终提交。详细流程见
[Agent 执行架构](/docs/architecture/agent-execution-architecture.md)。

## 6. 后台维护

`maintenance_jobs` 是 `state.db` 中的持久化任务队列。当前处理：

- 上下文摘要。
- 长期记忆提取。
- checkpoint 清理。

任务与 Turn 状态在同一事务入队，Core 崩溃后可以通过租约和重试恢复。后台维护允许滞后，但不得阻塞
用户的普通响应。

## 7. 观测通道

- **Telemetry Event**：描述领域事件，例如工具完成、记忆保存和上下文摘要。
- **System Trace**：按时间顺序串联 IPC、Agent、LLM、Tool 和响应写回。

二者均为 best-effort 诊断数据，不能用于业务恢复、计费或合规审计。

## 8. 下钻阅读

| 需要理解的问题 | 文档 |
|---|---|
| Agent 具体调用哪些函数 | [Agent 执行架构](/docs/architecture/agent-execution-architecture.md) |
| CLI 与 Core 如何解耦 | [CLI 架构](/docs/architecture/cli-architecture.md) |
| TUI 如何实现流式展示 | [TUI 架构](/docs/architecture/tui-architecture.md) |
| Core 如何组装与关闭组件 | [Core 架构](/docs/architecture/core-architecture.md) |
| 数据库表与事务如何工作 | [本地数据库与一致性](/docs/architecture/database-state-and-consistency.md) |
| 长任务如何暂停恢复 | [可恢复执行](/docs/architecture/resumable-execution.md) |
| 安全边界与威胁 | [安全模型](/docs/architecture/security-model.md) |
