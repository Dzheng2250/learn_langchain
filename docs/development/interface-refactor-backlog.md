# 接口化重构技术债务

> 文档状态：Draft
> 权威范围：Core Ports/Adapters 重构的待办、优先级和完成条件
> 维护触发：接口化重构完成、新增或取消技术债务

本文记录尚未完成的内部重构计划，不代表当前已实现能力。当前依赖边界见
[面向接口的 Core 设计](/docs/architecture/interface-driven-core.md)。

## 技术债务清单

后续优先级建议如下：

1. **Workspace / Session Lifecycle**
   Workspace 注册、Session 归档、硬删除、重置等能力应继续收敛到明确的 lifecycle service 和 store 端口中。
   当前 `SessionLifecycleService` 已经把状态查询拆给 `SessionStatusReader`，只读 status 不再混在归档、删除、重置等写路径里；
   RPC 返回字典由
   `src/core/session/responses.py` 构造，checkpoint cleanup 入队由
   `src/core/session/checkpoint_cleanup.py` 负责。后续如果继续拆，应优先把 Session 读写能力抽成更小的
   `SessionLifecycleStore`，而不是让 lifecycle service 直接依赖具体 workspace repository 方法。

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
   `ExecutionRepository` 仍保留 begin/resume/pause 等入口，后续可继续按“创建与恢复”“暂停状态更新”拆成更小 adapter 或 query helper。

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

