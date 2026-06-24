# 内部端口与 Adapter 扩展指南

> 文档状态：Current
> 权威范围：新增内部 Port、Adapter、存储后端和对应测试的开发流程
> 维护触发：Ports/Adapters 目录、DI 装配或契约测试要求变化

本文面向扩展 Core 内部实现的开发者。当前架构和依赖原则见
[面向接口的 Core 设计](/docs/architecture/interface-driven-core.md)。

## 本文负责

- 新增存储后端和内部 Port 的步骤。
- Adapter、DI 装配和 Contract Test 要求。

## 本文不负责

- 不解释当前 Core 架构；见 Architecture 文档。
- 不定义外部 RPC、Tool 或 Provider 扩展契约；见 [Core 平台扩展指南](/docs/development/platform-extension.md)。
- 不记录尚未完成的重构项；见 [接口化重构技术债务](/docs/development/interface-refactor-backlog.md)。

## 1. 如何新增存储后端

以后如果要把会话历史从 SQLite 扩展到 JSONL 文件，不能在 `AgentTurnService` 或 `CompletedTurnCommitter` 里写文件逻辑。

正确路径是：

```text
新增 Adapter
  -> 实现 ports 中的 Protocol
  -> 在 CoreContainer / factory 中按配置选择 Adapter
  -> 复用同一组 contract tests
```

以 `FileConversationHistoryStore` 为例：

1. 在 `src/core/adapters/file/` 中实现 `ConversationHistoryStore`。
2. 明确文件布局，例如按 Workspace、Session 和日期拆分 JSONL。
3. 保证 `append_turn()` 至少不会产生半行 JSON。
4. 实现 `load_turn()` 和 `rebuild_recent()`，返回与 SQLite 版本等价的 LangChain message。
5. 不在业务服务里判断“当前是文件还是 SQLite”。
6. 用同一份 contract test 跑 SQLite 和 File 两个实现。

如果新后端无法提供原子事务，就不能直接实现完整 `StateUnitOfWork`，只能先作为历史副本、导出后端或调试后端。

## 2. 如何新增端口

新增端口前必须先确认它表达的是稳定业务能力，而不是某个数据库表的 CRUD。

推荐流程：

```text
业务场景
  -> 命名能力
  -> 定义 Protocol
  -> 写 Fake 实现测试业务服务
  -> 写 SQLite Adapter
  -> 在 CoreContainer 注入
```

端口应保持小而专。不要把前台召回、后台写入、运维查询全部塞进同一个接口。

Session 生命周期是一个现有示例：`SessionLifecycleService` 只依赖
`SessionLifecycleStore`，SQLite 的 Workspace repository、消息历史和 checkpoint thread
查询由 `SQLiteSessionLifecycleStore` 组合。Agent 执行则按用途拆成
`ExecutionLifecycleStore`、`ExecutionPauseStore`、`ExecutionSliceStore` 和 `ExecutionFailureStore`，避免一个循环控制器
依赖完整 repository。新增后端时应实现对应小端口，并在 `CoreContainer` 替换 provider，
不能在 service 中加入 `if backend == ...`。

例如长期记忆可拆成：

```text
MemoryRetrievalStore     # 前台 Turn 只需要召回
MemoryWriteStore         # 后台维护才需要提取和保存
MemoryInspectionStore    # 调试或运维才需要查询失败状态
```

## 3. 测试要求

接口化重构必须配套测试，否则只是移动代码。

最低要求：

- Adapter 单元测试：验证 SQLite 实现保持旧行为。
- Contract Tests：同一组行为测试可套到 SQLite、InMemory、File 等实现。
- 边界测试：application service 层不得 import `sqlite3` 或 `src.core.adapters.sqlite`。
- 回归测试：finalization、agent service、local state、documentation tests 必须通过。

模型 Provider 扩展也遵循相同规则：业务模块只能导入 `src/core/llm/contracts.py`；新供应商实现
放在实现模块中，并由 `CoreContainer` 选择。不得因为新增供应商而在 Agent、Memory 或 Tool 中
加入供应商分支。

应用服务构造器也属于依赖边界。类似 `AgentTurnService` 的 facade 只应接收已组装协作者，
不能为了测试方便重新加入 provider、repository 或 worker 的可选 fallback。该规则也适用于
`TurnExecutionLoop` 等内部编排器：observer、错误策略、暂停策略和配置必须由组合根注入，
不能在编排器内部以 `dependency or ConcreteType()` 方式偷偷选择实现。跨模块请求编排应依赖
`DiagnosticTurnStreamer`、`RuntimeGraphProvider` 这类行为 Protocol，而不是导入具体 service 类。
测试需要替换依赖时，
应在 `tests/support` 中建立显式 assembly helper，生产装配仍只保留在 `CoreContainer`。

新增 adapter 时，测试重点应覆盖：

```text
消息顺序
message_id 稳定性
raw 数据不丢失
事务回滚
摘要不被 fast context 覆盖
后台任务与业务状态同事务入队
```

