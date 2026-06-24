# Agent Runtime 扩展指南

> 文档状态：Current
> 权威范围：新增模型用途、工具、运行限制和事件消费者的工程步骤
> 维护触发：Agent Runtime 扩展点、注册方式或必需测试变化

## 本文负责

- 说明如何通过现有扩展点增加 Agent Runtime 能力。
- 规定扩展时必须同步的类型、注册、配置和测试。
- 防止新增能力绕过 Provider、ToolRegistry、运行限制或 EventBus 边界。

## 本文不负责

- 不解释 Agent Runtime 为什么采用当前结构；见[Agent 执行架构](/docs/architecture/agent-execution-architecture.md)。
- 不定义公开 RPC 或流式事件字段；见 `/docs/api/`。
- 不登记尚未实现的产品能力；见[路线图与已知限制](/docs/product/roadmap-and-known-limitations.md)。

## 扩展原则

1. 业务服务依赖 Port 或 Registry，不直接创建 Provider、工具、sink 或数据库连接。
2. 新能力由 `CoreApp` 或 Workspace Runtime 的组合边界注入。
3. 配置、领域枚举和 Prompt 分开管理，不在业务代码中散布魔法字符串。
4. 每个扩展点必须有最小契约测试，并补充对应风险场景测试。

## 增加新的模型用途

1. 在 `LlmPurpose` 增加用途，明确该调用属于前台还是后台。
2. 通过构造参数接收 `ModelProvider`，不要在业务模块实例化具体服务商客户端。
3. 调用 `provider.create_chat_model()`，并传入稳定用途。
4. 增加 Fake Provider 测试，覆盖成功、服务商拒绝和不可重试错误。
5. 若用户可感知失败，补充错误来源映射和 API 文档。

## 增加工具

1. 实现 Workspace 绑定的工具 factory，所有文件路径必须经过 Workspace 安全解析。
2. 在 `create_workspace_toolset()` 注册一条 `ToolSpec`。
3. 明确 `audiences` 和 `risk`，不要维护平行工具列表。
4. 通过 `ObservedToolNode` 执行，避免在单个工具内重复实现公共观测逻辑。
5. 增加参数校验、路径安全、受众筛选、超时或取消、工具边界事件测试。

## 增加运行限制

1. 扩展 `RunLimits` 和对应配置模型。
2. 扩展类型化 `StopReason`。
3. 在拥有该资源计数的执行边界实施限制，不在多个工具中分别判断。
4. 将停止原因写入内部结果、流式事件和可恢复 Execution 状态。
5. 测试限制命中、恢复后累计和不同 Execution 隔离。

## 增加事件消费者

1. 根据用途判断它属于请求流式事件还是 Telemetry，不得混用两条通道。
2. Telemetry 消费者实现稳定 sink 接口，并由 `EventBus` 和组合根装配。
3. sink 自行处理缓冲、重试和关闭，失败不得抛入 Agent 业务链。
4. 默认只记录安全摘要，不复制完整 Prompt、工具载荷或敏感配置。
5. 增加慢消费者、队列满、写入失败和关闭 drain 测试。

## 提交前检查

- Architecture 只在运行结构确实变化时更新。
- API 只在外部契约变化时更新。
- Reference 同步配置、枚举和 Schema 事实。
- Product 路线图同步能力状态。
- 运行相关 unit、integration、contract 与非功能测试。