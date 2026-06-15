# 文档中心

本文档中心按“使用者需要解决的问题”分类，而不是按代码目录堆放文件。

## 快速入口

| 目标 | 首先阅读 |
|---|---|
| 开发 CLI、TUI 或其他前端 | [前端接入指南](/docs/api/tui-client-guide.md) |
| 查询 Core 支持哪些 RPC | [RPC 方法参考](/docs/api/rpc-reference.md) |
| 理解流式 token、工具步骤和完成事件 | [流式事件参考](/docs/api/streaming-events.md) |
| 理解 Agent 从请求到完成的完整流程 | [Agent 执行架构](/docs/architecture/agent-execution-architecture.md) |
| 理解 Session、SQLite 和 checkpoint | [本地数据库与一致性](/docs/architecture/database-state-and-consistency.md) |
| 部署和配置项目 | [部署指南](/docs/operations/deployment.md) 与 [配置参考](/docs/reference/configuration-reference.md) |
| 排查一次跨层请求 | [系统级 Trace](/docs/architecture/system-tracing.md) |

## 文档分类

### `api/`：对外接口契约

面向 CLI、TUI、GUI 和其他客户端开发者。这里描述“可以发送什么、会收到什么、失败后怎么办”。

- [IPC 协议](/docs/api/ipc-protocol.md)
- [RPC 方法参考](/docs/api/rpc-reference.md)
- [流式事件参考](/docs/api/streaming-events.md)
- [错误与恢复参考](/docs/api/error-reference.md)
- [TUI 与其他前端接入指南](/docs/api/tui-client-guide.md)

### `architecture/`：当前有效架构

描述当前代码如何工作、组件职责及调用关系。架构文档不是对外协议；前端不得依赖其中的内部类或数据库表。

- [Core 架构](/docs/architecture/core-architecture.md)
- [CLI 架构](/docs/architecture/cli-architecture.md)
- [Agent 执行架构](/docs/architecture/agent-execution-architecture.md)
- [本地数据库与一致性](/docs/architecture/database-state-and-consistency.md)
- [最终响应与 checkpoint 一致性](/docs/architecture/response-finalization-and-checkpoint-consistency.md)
- [可恢复执行](/docs/architecture/resumable-execution.md)
- [记忆管理](/docs/architecture/memory-management.md)
- [Telemetry Event](/docs/architecture/event-system.md)
- [系统级 Trace](/docs/architecture/system-tracing.md)

### `reference/`：可查阅的事实清单

- [配置参数参考](/docs/reference/configuration-reference.md)

### `operations/`：部署、迁移与运维

- [部署指南](/docs/operations/deployment.md)
- [本地状态迁移](/docs/operations/local-state-migration.md)

### `quality/`：非功能需求与测试

- [测试结构与运行指南](/docs/quality/testing-guide.md)
- [非功能需求](/docs/quality/non-functional-requirements.md)
- [非功能测试](/docs/quality/non-functional-testing.md)
- [本地优先状态测试](/docs/quality/local-first-testing.md)

### `decisions/`：设计决策与取舍

解释“为什么选择当前方案”。实现变化后应同步检查这些决策是否仍然成立。

- [本地优先状态的原因与审查](/docs/decisions/local-first-rationale-and-review.md)
- [CLI / Core 双进程与 JSON-RPC 设计决策](/docs/decisions/cli-core-json-rpc.md)
- [Workspace 隔离与迁移决策](/docs/decisions/workspace-isolation-and-migration.md)
- [配置、常量与 Prompt 管理边界](/docs/decisions/configuration-and-domain-constants.md)

### `history/`：历史 Review 与已完成整改

仅用于追溯演进过程，不应作为当前行为的唯一依据。

- [PR #3 Review 整改](/docs/history/pr-3-review-hardening.md)
- [PR #3 Review 回复](/docs/history/pr-3-review-response.md)
- [Workspace 隔离历史审查](/docs/history/workspace-isolation-review.md)

## 文档之间的关系

```text
API 契约
  告诉外部调用者如何使用系统
        |
        v
Architecture
  解释 Core 内部如何履行契约
        |
        +--> Decisions：解释为何选择该方案
        +--> Quality：定义延迟、可靠性和测试要求
        +--> Operations：说明如何部署、迁移和排障
        +--> History：保留已被替代或完成的讨论
```

## 维护规则

1. 新增或修改 RPC 时，同时更新 `src/ipc/models.py`、Handler、`api/rpc-reference.md` 和测试。
2. 新增或修改 `agent.event` 时，同时更新 `api/streaming-events.md` 和前端兼容测试。
3. 修改架构时更新 `architecture/`；记录重要取舍时更新 `decisions/`。
4. Review 回复和一次性整改说明放入 `history/`，不得混入当前接口参考。
5. 配置变化必须同步更新代码、`.env.example` 和 `reference/configuration-reference.md`。
6. 文档链接使用从仓库根开始的 `/docs/...` 路径，避免移动文件后产生大量相对链接失效。
