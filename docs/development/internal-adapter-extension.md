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
- 不定义外部 RPC、Tool 或 Provider 扩展契约；见 [扩展指南](/docs/api/extension-guide.md)。
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

新增 adapter 时，测试重点应覆盖：

```text
消息顺序
message_id 稳定性
raw 数据不丢失
事务回滚
摘要不被 fast context 覆盖
后台任务与业务状态同事务入队
```

