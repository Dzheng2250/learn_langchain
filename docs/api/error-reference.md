# 错误与恢复参考

> 文档状态：Current
> 权威范围：公开错误类别、用户可见含义和客户端恢复策略
> 维护触发：错误码、Provider 错误分类或恢复行为变化

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

## LLM 失败来源字段

模型调用可能发生在多处：

- 当前用户对话的父 Agent。
- 子 Agent 或工具内部。
- 后台上下文摘要。
- 后台长期记忆提取。

因此客户端不能只显示“模型调用失败”。Core 会在当前请求的流式事件中附带以下字段：

| 字段 | 含义 | 当前常见值 |
|---|---|---|
| `failure_source` | 报告失败的系统 | `agent_turn` |
| `failure_scope` | 失败影响范围 | `current_turn` |
| `failure_stage` | 失败发生阶段 | `parent_model_provider` / `parent_graph` / `context_summary` / `memory_extraction` |
| `user_action` | 推荐用户操作 | `revise_input_and_retry` / `resume_later` |

当 `failure_source=agent_turn` 且 `failure_scope=current_turn` 时，表示失败属于本次对话请求本身。
当 `auto_recovered=true` 且 `failed_turn_saved=false` 时，表示 Core 已自动释放 Session，本轮失败输入没有写入
对话历史，用户可以修改输入后继续普通对话，不需要执行 `session resume` 或 `session discard`。

后台摘要和长期记忆提取失败属于维护任务，不应让当前对话失败。客户端应通过 `session.status` 中的
`maintenance.failed` 计数和 `maintenance.recent_failures[].job_type` 判断失败来源。例如：

- `job_type=context_summary`：后台上下文摘要压缩失败。
- `job_type=memory_extract`：后台长期记忆提取失败。
- `job_type=checkpoint_cleanup`：后台 checkpoint 清理失败，通常不涉及 LLM。

这样前端可以区分“当前这轮对话的父 Agent 调用模型失败”和“后台派生任务失败”，避免把所有 LLM
错误都归因到用户当前输入。

## 前端展示原则

- 展示 Core 提供的安全 `message`。
- 将“是否可重试”和“是否可恢复”分开表达。
- 使用 `failure_source/failure_scope/failure_stage` 选择用户友好说明，避免用户误以为所有 LLM 失败都来自当前输入。
  普通界面不应直接打印这些字段名；调试界面或 verbose 模式可以展示原始字段。
- 不直接展示 API key、完整 Provider 原始响应或 traceback。
- 非幂等请求发生连接错误时，不提供“一键自动重试”。
