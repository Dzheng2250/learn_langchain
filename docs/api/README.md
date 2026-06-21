# API 与通信文档索引

> 文档状态：Current
> 权威范围：外部接口文档入口、阅读顺序和契约边界
> 维护触发：新增、移动、废弃或重命名 API 文档

本文是 `docs/api/` 的目录级入口。它面向 CLI、TUI、GUI、脚本和第三方前端开发者，说明应该从哪里了解
Core daemon 的外部通信能力。

## 本文负责

- 组织 IPC、RPC、流式事件、错误、CLI 和扩展文档。
- 说明前端接入时的推荐阅读顺序。
- 明确 API 文档与内部架构文档的职责边界。

## 本文不负责

- 不解释 Core 内部如何实现 Agent、状态库或工具系统；这些属于 `/docs/architecture/`。
- 不记录为什么选择某个协议方案；这些属于 `/docs/decisions/`。
- 不说明部署、备份或升级步骤；这些属于 `/docs/operations/`。

## 推荐阅读顺序

| 读者目标 | 阅读顺序 |
|---|---|
| 实现新的 TUI/GUI 前端 | [IPC 协议](/docs/api/ipc-protocol.md) -> [RPC 方法参考](/docs/api/rpc-reference.md) -> [流式事件参考](/docs/api/streaming-events.md) -> [前端接入指南](/docs/api/tui-client-guide.md) |
| 调试一次请求失败 | [错误与恢复参考](/docs/api/error-reference.md) -> [RPC 方法参考](/docs/api/rpc-reference.md) -> [系统 Trace](/docs/architecture/system-tracing.md) |
| 使用命令行 | [CLI 命令参考](/docs/api/cli-reference.md) |
| 使用内置 TUI | [TUI 使用与命令参考](/docs/api/tui-reference.md) |
| 判断变更是否破坏兼容 | [协议兼容性](/docs/api/protocol-compatibility.md) -> [RPC 方法参考](/docs/api/rpc-reference.md) -> [流式事件参考](/docs/api/streaming-events.md) |

## 文档分组

### 1. Transport 与鉴权

- [IPC 协议](/docs/api/ipc-protocol.md)

负责说明 TCP、NDJSON、JSON-RPC envelope、token 鉴权、连接生命周期和身份字段。

不负责说明具体业务方法的参数；业务方法由 RPC 参考负责。

### 2. RPC 方法

- [RPC 方法参考](/docs/api/rpc-reference.md)

负责列出 Core daemon 当前公开的 RPC 方法、参数、结果、幂等性和重试风险。

不负责说明流式事件里的每个前端渲染细节；事件由流式事件参考负责。

### 3. 流式事件

- [Agent 流式事件参考](/docs/api/streaming-events.md)

负责说明 `agent.chat` 和 `session.resume` 执行期间 Core 主动推送的 `agent.event` 通知。

不负责解释 Agent 为什么产生这些事件；内部执行原因由 Agent 架构文档负责。

### 4. 错误与恢复

- [错误与恢复参考](/docs/api/error-reference.md)

负责说明前端会看到的错误类型、错误来源、可恢复性和建议交互。

不负责保存或恢复业务状态；状态恢复机制由架构文档负责。

### 5. 前端与 CLI

- [CLI 命令参考](/docs/api/cli-reference.md)
- [前端接入指南](/docs/api/tui-client-guide.md)

负责说明用户命令和前端客户端的接入职责。

前端不得直接访问 `state.db`、`checkpoints.db`、工具实现或 Agent 内部服务。所有业务操作必须通过
Core daemon 的 RPC。

### 6. 扩展与兼容

- [协议兼容性](/docs/api/protocol-compatibility.md)

负责说明如何扩展公开能力，以及哪些变化属于兼容或破坏性变更。

## API 文档写作约束

新增或修改 API 文档时：

1. 只记录外部调用者可以依赖的行为。
2. 不暴露内部数据库表、内部类名或未稳定的实现细节，除非该实现已经成为外部契约。
3. 示例必须包含鉴权、请求 ID、方法名和关键参数。
4. 错误说明必须包含前端应如何处理，而不是只写异常名称。
5. 新增 RPC、CLI 命令或流式事件时，同步更新本文、[文档中心](/docs/README.md)、
   [文档登记表](/docs/governance/document-register.md) 和契约测试。
