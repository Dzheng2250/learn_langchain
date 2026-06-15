# 错误与恢复参考
前端需要区分协议错误、连接错误和 Agent 执行错误。它们的恢复方式不同。

## JSON-RPC 错误

| 错误码 | 含义 | 推荐处理 |
|---:|---|---|
| `-32700` | Parse error | 修复 NDJSON/UTF-8 编码；连接已关闭 |
| `-32600` | Invalid Request | 修复 JSON-RPC 外层结构 |
| `-32601` | Method not found | 检查客户端与 daemon 版本 |
| `-32602` | Invalid params | 按参数模型修复请求 |
| `-32603` | Internal error | 不自动重试非幂等方法；查询状态和日志 |
| `-32001` | Unauthorized | 重新读取 daemon token，必要时重启 daemon |

## 连接错误

### 连接拒绝或 token 文件不存在

Core 通常未启动。客户端可以提示运行 `learn-agent start`。

### 请求中途断线

请求可能仍在 Core 中运行。对于 `agent.chat` 和 `session.resume`：

1. 不要自动重发原请求。
2. 调用 `session.status`。
3. 若存在可恢复 Execution，提示用户选择 `session.resume` 或 `session.discard`。

### 无效响应

通常代表客户端与 Core 版本不兼容，或传输内容损坏。应停止解析并提示重启/升级。

## Provider 错误分类

Core 将不同服务商错误归一为：

| 分类 | 通常动作 | 含义 |
|---|---|---|
| `content_rejected` | terminate | 输入被内容审查拒绝，原请求不应自动重试 |
| `invalid_request` | terminate | 请求参数或内容不被服务商接受 |
| `authentication` | terminate | API 密钥或鉴权配置错误 |
| `rate_limited` | pause | 达到限流，稍后可恢复 |
| `service_unavailable` | pause | 服务商暂时不可用 |
| `network` | pause | 网络错误 |
| `unknown` | pause | 无法可靠分类 |

`error_action=terminate` 会自动释放 Session 的 pending Execution，但不会删除已有会话历史。详细设计见[服务商错误处理](/docs/architecture/provider-error-handling.md)。

## 前端展示原则

- 展示 Core 提供的安全 `message`。
- 将“是否可重试”和“是否可恢复”分开表达。
- 不直接展示 API key、完整 Provider 原始响应或 traceback。
- 非幂等请求发生连接错误时，不提供“一键自动重试”。
