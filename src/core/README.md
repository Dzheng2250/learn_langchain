# Core Architecture

`src/core` 是用户级 daemon 的业务执行端。它不读取终端输入，也不直接渲染用户输出。

配置默认值、环境变量、参数单位和调整风险见
[`/docs/reference/configuration-reference.md`](/docs/reference/configuration-reference.md)。

```text
CoreApp
  -> Transport + RpcRouter + Handlers
  -> LocalStateDatabase + LocalWorkspaceRepository
  -> WorkspaceRuntimeRegistry
  -> AgentTurnService
  -> TurnFinalizer + MaintenanceScheduler + ExecutionRecoveryCoordinator
```

依赖约束：

1. CLI 只通过 `src/ipc` 调用 Core。
2. Transport 不依赖 Agent、Memory 或 Tools。
3. AgentTurnService 不依赖 CLI、RPC 或 TCP。
4. WorkspaceRuntime 中的工具和 graph 永久绑定不可变 WorkspaceContext。
5. Session、消息和长期记忆必须保存 Workspace 身份；Agent Turn 事件携带 Workspace 身份，
   daemon 生命周期事件允许没有 Workspace。
6. CoreApp 是组合根，具体组件通过构造函数注入。

详细设计：

- [`docs/README.md`](/docs/README.md)
- [`/docs/reference/configuration-reference.md`](/docs/reference/configuration-reference.md)
- [`/docs/architecture/core-architecture.md`](/docs/architecture/core-architecture.md)
- [`/docs/decisions/workspace-isolation-and-migration.md`](/docs/decisions/workspace-isolation-and-migration.md)
- [`/docs/history/workspace-isolation-review.md`](/docs/history/workspace-isolation-review.md)
- [`/docs/architecture/agent-execution-architecture.md`](/docs/architecture/agent-execution-architecture.md)
- [`/docs/architecture/memory-management.md`](/docs/architecture/memory-management.md)
- [`/docs/architecture/event-system.md`](/docs/architecture/event-system.md)
- [`/docs/quality/non-functional-requirements.md`](/docs/quality/non-functional-requirements.md)
- [`/docs/quality/non-functional-testing.md`](/docs/quality/non-functional-testing.md)
- [`/docs/history/pr-3-review-hardening.md`](/docs/history/pr-3-review-hardening.md)
- [`/docs/operations/deployment.md`](/docs/operations/deployment.md)
- [`/docs/architecture/local-first-session-state.md`](/docs/architecture/local-first-session-state.md)
- [`/docs/architecture/resumable-execution.md`](/docs/architecture/resumable-execution.md)
- [`/docs/operations/local-state-migration.md`](/docs/operations/local-state-migration.md)
- [`/docs/quality/local-first-testing.md`](/docs/quality/local-first-testing.md)
- [`/docs/decisions/local-first-rationale-and-review.md`](/docs/decisions/local-first-rationale-and-review.md)
- [`/docs/architecture/database-state-and-consistency.md`](/docs/architecture/database-state-and-consistency.md)
- [`/docs/architecture/response-finalization-and-checkpoint-consistency.md`](/docs/architecture/response-finalization-and-checkpoint-consistency.md)
- [`/docs/decisions/configuration-and-domain-constants.md`](/docs/decisions/configuration-and-domain-constants.md)
