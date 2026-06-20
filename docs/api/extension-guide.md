# 扩展开发指南

> 文档状态：Current
> 权威范围：新增 Tool、Skill、Provider、RPC、Telemetry Sink 的稳定扩展路径
> 维护触发：扩展点接口或组合根变化

## 本文负责

- Tool、Skill、Provider、RPC 和 Telemetry Sink 的稳定扩展入口。
- 扩展能力需要同步的测试和文档。

## 本文不负责

- 不说明内部存储 Port/Adapter 扩展；见 Development 内部扩展指南。
- 不记录尚未实现的重构计划。


## 1. 通用原则

新增能力应通过已有边界接入，而不是在多个业务函数中直接修改全局状态。

- CoreApp 是进程级组合根，负责创建和关闭共享组件。
- WorkspaceRuntimeFactory 负责 Workspace 绑定的图、工具和 Skill。
- Handler 只做协议到应用服务的适配。
- AgentTurnService 负责编排，不直接实现 Tool、Transport 或数据库细节。
- 通用观测放在 ToolNode、Provider wrapper 或 EventBus 边界。

新增能力前先完成[安全模型检查](/docs/architecture/security-model.md#6-新能力安全检查)。

## 2. 新增 Tool

主要文件：

```text
src/core/tools/<feature>.py
src/core/tools/registry.py
src/core/tools/catalog.py
```

步骤：

1. 使用工厂函数创建绑定 Workspace 的 Tool，避免读取全局 Workspace。
2. 对文件路径使用 Workspace resolver，不接受任意绝对路径。
3. 设置明确的输入、输出、超时和长度限制。
4. 在 `create_workspace_toolset()` 注册 ToolAudience 和 ToolRisk。
5. 通用开始、完成和失败事件由 `ObservedToolNode` 记录；Tool 内部只记录特有领域事件。
6. 增加单元测试、安全边界测试和 Agent 集成测试。
7. 更新功能需求、工具安全说明和必要的 Prompt。

风险等级：

- `READ_ONLY`：只读取或计算。
- `CONTROLLED_EXECUTION`：执行受限命令或产生受控副作用。
- `DELEGATION`：启动子 Agent。

## 3. 新增 Skill

Skill 位于 Workspace 的：

```text
skills/<skill-name>/SKILL.md
```

Skill 文档应包含清晰的名称、描述、适用场景和执行说明。Skill 是按需注入的知识，不应：

- 绕过 Tool 权限。
- 包含密钥或环境文件内容。
- 假设可以访问 Workspace 外路径。
- 要求子 Agent 再次委托子 Agent。

## 4. 新增模型 Provider

主要接口：

```python
ModelProvider.create_chat_model(...)
ModelConfiguration.configuration_status()
ProviderErrorParser.parse(...)
```

步骤：

1. 实现 ModelProvider，不让调用方依赖具体 SDK。
2. 为不同 `LlmPurpose` 保留用途标记。
3. 若服务商错误结构不同，实现独立 ProviderErrorParser 并注册到 Registry。
4. 保证错误解析失败不会覆盖原始异常。
5. 通过 TracingModelProvider wrapper 接入 Trace，不在 Provider 内复制 Trace 逻辑。
6. 增加配置、错误分类、流式 token usage 和失败行为测试。

## 5. 新增 RPC

步骤：

1. 在 `src/ipc/models.py` 定义严格参数模型。
2. 在 `src/core/handlers/` 增加 Handler 方法。
3. 通过 Handler 的 `register()` 注册到 RpcRouter。
4. 明确方法是否幂等、能否自动重试、是否产生流式事件。
5. 增加 Router、Handler 和端到端 Transport 测试。
6. 更新 [RPC 参考](/docs/api/rpc-reference.md)、错误参考和兼容策略。

不得让 Transport 直接调用 Agent、数据库或工具。

## 6. 新增 Telemetry Sink

步骤：

1. 实现 Sink 的 `emit` 或批量写入能力。
2. IO Sink 默认通过 `BufferedEventSink` 包装。
3. Sink 失败必须被隔离，不能改变业务结果。
4. payload 必须经过脱敏和长度限制。
5. 在 `src/core/telemetry/factory.py` 的组合位置启用。
6. 增加慢 Sink、队列满和关闭 drain 测试。

## 7. 新增后台维护任务

步骤：

1. 增加稳定 `MaintenanceJobType`。
2. 在最小业务事务中通过 Outbox 方式入队。
3. 实现幂等 Handler，并定义去重键和重试行为。
4. 在 CoreApp 注册 Handler。
5. 确保任务失败不会撤销已经提交的用户回答。
6. 增加重启恢复、租约、重试和并发测试。

## 8. 新增内部存储接口或后端

内部存储扩展面向 Core 代码，不是公开 RPC。详细设计见
[面向接口的 Core 设计](/docs/architecture/interface-driven-core.md)。

新增存储能力时遵循以下规则：

1. 先在 `src/core/ports/` 定义业务能力 Protocol，不要暴露 SQL、表名、连接对象或文件句柄。
2. 在 `src/core/adapters/<backend>/` 实现具体后端。
3. 只允许 `CoreApp` 或专门 factory 根据配置选择后端。
4. 应用服务只能依赖 ports，不能直接 import `sqlite3` 或 `src.core.adapters.*`。
5. 对每个端口增加 contract test，用同一组测试验证 SQLite、Fake 或未来 File 后端。
6. 如果后端不能提供事务一致性，不能替换权威 `StateUnitOfWork`，只能作为副本、导出或缓存。

当前可用的后端选择变量是：

```text
LEARN_AGENT_CONVERSATION_HISTORY_BACKEND=sqlite
LEARN_AGENT_MEMORY_BACKEND=sqlite
LEARN_AGENT_TASK_BACKEND=sqlite
LEARN_AGENT_CHECKPOINT_BACKEND=sqlite
```

当前版本只支持生产级 `sqlite`。其他值必须在实现 adapter、组合根选择逻辑和契约测试后才能开放。

新增 store adapter 的最低验收标准：

1. 在 `src/core/ports/` 中只暴露业务能力，不暴露 SQL、表名、连接对象或文件句柄。
2. 在 `src/core/adapters/<backend>/` 中实现具体后端，业务服务不得直接 import 该目录。
3. 为对应端口增加 contract tests；同一组测试必须能验证 SQLite、Fake/InMemory，以及未来 File/PostgreSQL 后端。
4. 如果 adapter 参与成功 turn 的最小提交，必须证明它支持 Unit of Work 语义：消息写入、Session 更新、Execution 完成和维护任务入队要么一起提交，要么一起回滚。
5. 如果 adapter 只提供读取、副本或导出能力，文档必须明确它不是权威状态来源，不能被用于恢复任务。
6. `CoreApp` 或专门 factory 是唯一允许根据配置选择后端的位置。

会话历史后端尤其需要覆盖以下契约：

- `append_turn()` 后可以按 `turn_index` 读取完整 turn。
- `load_turn()` 返回顺序必须与原始消息写入顺序一致。
- `messages.raw` 等价信息不得丢失，尤其是 AI tool call、ToolMessage 和非字符串 content。
- `rebuild_recent()` 只能从已提交历史重建近期上下文，不能读取未提交或其他 Session 的消息。
- 空 Session 应返回空历史，而不是抛出存储实现相关异常。

## 10. ????? DI ????

???? `dependency-injector` ?? Core ???????????? service?adapter?repository?provider ? transport ???????????

1. ??????????????????? import `CoreContainer`?
2. ????????? `src/core/container.py`?? `CoreApp` ????????
3. ????????????? provider override ????? `Factory` ??????????`Singleton` ??????????
4. ????????????????????????? `CoreApp.close()` ???? lifecycle service ???????
5. ?????????????????????????????????????????????????????

DI ??????????????????????????????RPC ???????????


## 9. 完成定义

扩展完成必须同时满足：

- 代码边界符合现有依赖方向。
- 配置、超时、大小限制和安全风险明确。
- 单元、集成或契约测试覆盖关键边界。
- API、Architecture、Requirement 或 Decision 文档按需更新。
- 已知限制登记到统一路线图。
