# Core 平台扩展指南

> 文档状态：Current
> 权威范围：Core 内部扩展类型的入口、依赖方向和共同完成标准
> 维护触发：新增扩展类别、组合根规则或扩展指南变化

## 本文负责

- 为 Tool、Skill、Provider、RPC、Telemetry、维护任务和存储 Adapter 提供统一导航。
- 规定所有内部扩展都必须遵守的依赖、安全、测试和文档要求。
- 说明尚无专项指南的 Skill、RPC 与维护任务扩展步骤。

## 本文不负责

- 不定义外部 RPC 字段和兼容规则；见 `/docs/api/`。
- 不重复 Agent Runtime 或存储 Adapter 的完整扩展步骤；见对应专项指南。
- 不记录尚未实现的重构计划；见 Product 路线图或 Draft backlog。

## 1. 共同原则

新增能力应通过既有 Port、Registry、Factory 或 Handler 接入，不能在业务函数中创建共享基础设施。

1. `CoreApp` 是进程级 Composition Root，负责选择实现和生命周期。
2. Workspace 绑定能力由 Workspace Runtime 工厂创建，不能使用可变全局 Workspace。
3. Handler 只做协议与应用服务之间的适配，不实现领域业务。
4. 横切观测放在 ToolNode、Provider wrapper 或 EventBus 边界。
5. 新能力必须明确安全风险、资源上限、失败语义和关闭行为。

新增能力前执行[安全模型检查](/docs/architecture/security-model.md#6-新能力安全检查)。

## 2. 扩展类型导航

| 扩展类型 | 权威指南 |
|---|---|
| 模型用途、Tool、运行限制、事件消费者 | [Agent Runtime 扩展指南](/docs/development/agent-runtime-extension.md) |
| 内部 Port、Adapter 和存储后端 | [内部端口与 Adapter 扩展指南](/docs/development/internal-adapter-extension.md) |
| 公开 RPC 与事件契约 | [协议兼容策略](/docs/api/protocol-compatibility.md)和本篇 RPC 章节 |
| Skill | 本篇 Skill 章节 |
| 后台维护任务 | 本篇维护任务章节 |

## 3. 新增 Skill

1. 在 Workspace 的 `skills/<name>/SKILL.md` 定义元数据和正文。
2. 使用 Skill parser 与 store 加载，不在 Prompt 中硬编码 Skill 内容。
3. 明确 Skill 是否允许父 Agent、子 Agent 或两者使用。
4. 对非法 frontmatter、重复名称、路径逃逸和超长内容增加测试。
5. 若改变用户可见能力，同步 Product 和对应使用文档。

## 4. 新增 RPC

1. 在中立 IPC 模型层定义严格参数和结果模型。
2. 在 Router 注册方法，在 Handler 中完成协议到应用服务的转换。
3. 所有请求先完成 envelope、参数与鉴权验证，再调用业务服务。
4. 定义稳定错误码、断线行为和是否产生 notification。
5. 同步 [RPC 方法参考](/docs/api/rpc-reference.md)、兼容规则与 Router 契约测试。
6. CLI/TUI 只能通过公开 RPC 使用能力，不能导入 Core 私有模块。

## 5. 新增后台维护任务

1. 增加类型化任务类型，明确优先级、去重键、租约和最大重试次数。
2. 若任务由业务提交触发，使用 Transactional Outbox 与业务事实同事务入队。
3. Handler 必须幂等；重试不得重复产生业务副作用。
4. 在 Composition Root 注册 Handler，不在 Scheduler 中硬编码业务实现。
5. 任务失败不得撤销已经提交的用户回答，失败状态必须可查询。
6. 增加重启恢复、租约过期、指数退避、关闭和并发测试。

## 6. DI 与生命周期

项目使用 DI 容器和 Composition Root 管理依赖，但容器不能泄漏到业务模块：

- 只有 `CoreApp` 或专用 factory 解析容器 provider；
- service、repository、adapter、provider 和 transport 继续使用构造函数注入；
- 测试通过 provider override 替换依赖，不通过全局 monkey patch；
- 长生命周期资源使用 singleton，短生命周期对象使用 factory；
- 资源关闭顺序由 `CoreApp.close()` 统一控制。

DI 解决组装，不替代清晰接口，也不应隐藏业务依赖。

## 7. Hook 命名边界

不要把 Telemetry Event 当作通用 Hook 系统扩展。当前观测链路的权威入口是 `src.core.telemetry`；旧的 `src.core.hooks` 包已经移除，命名空间保留给未来真正的 Hook 模块。

如果后续需要真正的 Hook 模块，必须先明确触发点、同步/异步语义、失败隔离、是否允许修改输入输出，以及权限和测试契约。

## 8. 完成定义

扩展完成必须同时满足：

- 依赖方向符合 Port/Adapter 与组合根边界。
- 配置、超时、大小限制、安全风险和失败恢复明确。
- 单元、集成、契约或非功能测试覆盖关键边界。
- API、Architecture、Reference、Product 或 Decision 按职责同步。
- 已知限制只登记到统一路线图，不散落在扩展指南中。