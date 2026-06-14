# Core Architecture

`src/core` 是用户级 daemon 的业务执行端。它不读取终端输入，也不直接渲染用户输出。

配置默认值、环境变量、参数单位和调整风险见
[`docs/configuration-reference.md`](../../docs/configuration-reference.md)。

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

- [`docs/README.md`](../../docs/README.md)
- [`docs/configuration-reference.md`](../../docs/configuration-reference.md)
- [`docs/core-architecture.md`](../../docs/core-architecture.md)
- [`docs/workspace-isolation-and-migration.md`](../../docs/workspace-isolation-and-migration.md)
- [`docs/workspace-isolation-review.md`](../../docs/workspace-isolation-review.md)
- [`docs/agent-execution-architecture.md`](../../docs/agent-execution-architecture.md)
- [`docs/memory-management.md`](../../docs/memory-management.md)
- [`docs/event-system.md`](../../docs/event-system.md)
- [`docs/non-functional-requirements.md`](../../docs/non-functional-requirements.md)
- [`docs/non-functional-testing.md`](../../docs/non-functional-testing.md)
- [`docs/pr-3-review-hardening.md`](../../docs/pr-3-review-hardening.md)
- [`docs/deployment.md`](../../docs/deployment.md)
- [`docs/local-first-session-state.md`](../../docs/local-first-session-state.md)
- [`docs/resumable-execution.md`](../../docs/resumable-execution.md)
- [`docs/local-state-migration.md`](../../docs/local-state-migration.md)
- [`docs/local-first-testing.md`](../../docs/local-first-testing.md)
- [`docs/local-first-rationale-and-review.md`](../../docs/local-first-rationale-and-review.md)
- [`docs/database-state-and-consistency.md`](../../docs/database-state-and-consistency.md)
- [`docs/response-finalization-and-checkpoint-consistency.md`](../../docs/response-finalization-and-checkpoint-consistency.md)
- [`docs/configuration-and-domain-constants.md`](../../docs/configuration-and-domain-constants.md)
