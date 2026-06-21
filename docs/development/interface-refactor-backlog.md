# 接口化重构技术债务

> 文档状态：Draft
> 权威范围：Core Ports/Adapters 重构的待办、优先级和完成条件
> 维护触发：接口化重构完成、新增或取消技术债务

本文记录尚未完成的内部重构计划，不代表当前已实现能力。当前依赖边界见
[面向接口的 Core 设计](/docs/architecture/interface-driven-core.md)。

## 技术债务清单

后续优先级建议如下：

1. **Workspace / Session Lifecycle（本阶段已完成）**
   `SessionLifecycleStore` 已落地，`SessionLifecycleService` 和 `SessionStatusReader` 不再直接依赖
   `LocalWorkspaceRepository`、`LocalStateStore` 或 store factory。SQLite 细节集中在
   `SQLiteSessionLifecycleStore`、`SQLiteSessionStore` 和 `SQLiteConversationHistoryStore`，
   并由 `CoreContainer` 统一装配。RPC 返回字典仍由 `src/core/session/responses.py` 构造，
   checkpoint cleanup 入队仍由 `src/core/session/checkpoint_cleanup.py` 负责。
   后续工作不再扩张该端口；应转向为 Execution、checkpoint manager 和 maintenance inspection
   定义更小的能力接口，进一步消除 lifecycle service 对兼容 repository 的依赖。

   同期完成的 Agent facade 清理：`AgentTurnService` 已移除旧的 fallback 装配构造器，
   当前只接收三个已经组装好的协作者。生产装配唯一位于 `CoreContainer`；集成测试的
   自定义装配位于 `tests/support/agent_services.py`，不会回流到生产业务层。
   与 Agent 直接协作的 parent graph、Workspace runtime、subagent、上下文摘要、记忆提取和
   文件摘要也已移除 `OpenAICompatibleProvider` fallback；`ModelProvider`、`ModelConfiguration`
   和 `LlmPurpose` 已移动到 `llm/contracts.py`。业务模块只导入契约，具体 provider 只在组合根创建。

   Agent Core 的请求与循环边界也已收紧：`AgentRequestStreamService` 通过 `AgentSessionStore`
   解析 Workspace/Session，不再直接查询具体 Workspace repository；`TurnExecutionLoop`
   不再在内部创建 observer、错误处理器、暂停处理器或配置。上述协作者统一由
   `CoreContainer` 组装，测试装配则集中在 `tests/support/agent_services.py`。
   前台上下文读取也已改用 `SessionStore + MemoryRetrievalStore`；`TurnExecutionLoop` 不再创建
   `LocalStateStore`，`TurnFinalizer` 不再接收无用 Store 参数。下一步应继续处理
   请求路由已进一步改为依赖 `DiagnosticTurnStreamer`、`ExecutionLifecycleController`、
   `RuntimeGraphProvider` 和 `LockedTurnStreamer`；诊断 Turn 也已使用 `SessionStore`。
   `RuntimeGraphResolver` 通过 `WorkspaceRuntimeProvider` 获取 runtime，不再依赖具体 Registry。
   `AgentServiceLifecycle` 也已改为依赖 `StateInitializer`，生产环境直接注入
   `LocalStateDatabase`，不再创建兼容 Store。下一步主要处理诊断之外的后台 maintenance
   中仍保留的兼容 store factory，但不能把进程初始化职责重新放回前台执行链路。

2. **Task Store**
   私有任务规划已经拆出 `TaskPlanValidator` 和 `TaskQueryStore`，分别承载纯规则校验与任务查询装配。
   任务写入 SQL 已拆到 `TaskMutationStore`，Execution 身份校验已拆到 `TaskExecutionContextGuard`。
   `TaskRepository` 现在主要负责事务编排：校验输入、读取当前图、调用 mutation helper、再验证依赖状态。
   后续如果要支持文件或 PostgreSQL 任务后端，应继续抽出 `TaskStore` 端口，避免上层依赖具体 repository。

3. **Execution Store**
   `PendingExecution` 和 SQLite 行映射已经拆到 `src/core/state/execution_models.py`。
   `get_pending()` 和 `get_attached()` 等只读查询已经拆到 `ExecutionQueryStore`。
   checkpoint 清理、缺失和重启恢复对账已经拆到 `ExecutionCheckpointStore`。
   Slice 创建、完成和预算计数已经拆到 `ExecutionSliceStore`。
   complete/discard/terminate 这类“结束 Execution 并释放 Session”的写入已经拆到 `ExecutionReleaseStore`。
   Agent Core 已通过 `ExecutionLifecycleStore`、`ExecutionPauseStore`、`ExecutionSliceStore` 和 `ExecutionFailureStore`
   使用这些能力，构造器不再暴露 `execution_repository`。`ExecutionRepository` 目前作为 SQLite
   兼容 adapter 同时满足这些结构化端口，仍保留 begin/resume/pause 等入口。后续应把具体实现
   继续拆成“创建与恢复”“暂停状态更新”小 adapter，再由组合根分别注入，而不是扩张现有端口。

4. **Migration Orchestration**
   PostgreSQL 到本地状态的迁移入口仍是 `LocalStateMigration`，但源库计数检查已经拆到
   `LocalStateMigrationInspector`，源库清理已经拆到 `LocalStateSourcePruner`。后续如果继续拆，应优先拆 `_copy()` 中的 workspace/session、messages、memories、events 四段复制逻辑。
   旧 PostgreSQL workspace schema 迁移的 `WorkspaceMigration` 也不再包含 `pg_dump` / Docker 备份细节；
   备份逻辑已经拆到 `src/core/database/backup.py`，迁移编排只依赖 `create_database_backup()`。

5. **Memory Write / Read Contract Tests**
   本地 SQLite 记忆读取和写入已经拆出 adapter。兼容 PostgreSQL memory facade 也已经把格式化、检索辅助和写入事务拆到 `formatting.py`、`retrieval.py`、`writer.py`。
   后续还需要更完整的 contract tests，方便未来替换为向量检索或混合后端。

6. **File Conversation History 原型**
   文件历史后端可以作为实验实现，但不能直接替代权威状态库，除非它能满足同等的事务和恢复语义。

7. **Context Summary Execution**
   `AgentContextManager` 仍负责上下文窗口、摘要触发判断和状态更新，但 LLM 摘要调用已经拆到
   `ContextSummaryExecutor`。这让上下文状态算法不直接依赖 provider 创建、prompt 构造、token usage
   解析和 telemetry span。后续如果更换摘要模型或加入无模型摘要策略，应优先扩展 executor，而不是继续扩大 manager。

8. **Maintenance Queue**
   `MaintenanceRepository` 仍是后台维护任务的兼容门面，但只读状态查询已经拆到
   `MaintenanceInspectionStore`，SQLite row 到领域对象的转换也已经拆到 `src/core/maintenance/records.py`。
   后续如果要替换维护队列后端，应继续把 enqueue、claim、succeed/fail/requeue 拆成明确的 `MaintenanceQueue`
   和 `MaintenanceLeaseStore` 端口。

