# 错误与恢复参考

> 文档状态：Current
> 权威范围：公开错误类别、用户可见含义和客户端恢复策略
> 维护触发：错误码、Provider 错误分类或恢复行为变化

## 本文负责

- 说明 JSON-RPC、连接、Agent 执行和 Provider 错误的用户可见含义。
- 说明前端在失败后应该提示用户做什么。

## 本文不负责

- 不解释内部解析器和重试实现；见 [通用 LLM 错误恢复](/docs/architecture/provider-error-recovery.md)。
- 不保存服务端堆栈或完整 Provider 原始响应。

## JSON-RPC 错误

| 错误码 | 含义 | 推荐处理 |
|---:|---|---|
| `-32700` | Parse error | 修复 NDJSON/UTF-8 编码；当前连接会关闭。 |
| `-32600` | Invalid Request | 修复 JSON-RPC 外层结构。 |
| `-32601` | Method not found | 检查客户端与 daemon 版本。 |
| `-32602` | Invalid params | 按参数模型修复请求。 |
| `-32603` | Internal error | 不自动重发非幂等方法；查询状态和日志。 |
| `-32001` | Unauthorized | 重新读取 daemon token，必要时重启 daemon。 |

## 连接错误

| 场景 | 推荐处理 |
|---|---|
| 连接拒绝或 token 文件不存在 | Core 通常未启动，提示运行 `learn-agent start`。 |
| 请求中途断线 | 不自动重发 `agent.chat` 或 `session.resume`；先调用 `session.status`。 |
| 无效响应 | 停止解析并提示客户端与 Core 版本可能不兼容。 |

## Provider 错误分类

Core 先执行模型级自动重试；只有重试不适用或耗尽后，才进入下表的最终处理。

| 分类 | 是否模型级重试 | 最终动作 | 含义 |
|---|---:|---|---|
| `rate_limited` | 是 | pause | 短期限流，稍后可能恢复。 |
| `service_overloaded` | 是 | pause | 服务商过载。 |
| `service_unavailable` | 是 | pause | 408/5xx/529 等临时不可用。 |
| `timeout` | 是 | pause | 模型请求超时。 |
| `connection_failed` | 是 | pause | 网络连接失败。 |
| `stream_interrupted` | 是 | pause | 流式输出中断。 |
| `usage_limit` | 否 | terminate | 账户用量或套餐限制，通常不能短时间自动恢复。 |
| `quota_exhausted` | 否 | terminate | 配额耗尽。 |
| `content_rejected` | 否 | terminate | 输入或请求被内容审查拒绝。 |
| `context_length_exceeded` | 否 | terminate | 上下文超过模型限制；本轮不会自动压缩重放。 |
| `invalid_request` | 否 | terminate | 请求参数或内容不被服务商接受。 |
| `authentication` | 否 | terminate | API key 或鉴权配置错误。 |
| `model_not_found` | 否 | terminate | 模型名称不存在或账号不可用。 |
| `unknown` | 否 | pause | 无法安全分类，保留现场等待人工判断。 |

`terminate` 表示失败 Turn 不写入正式消息历史，Session 自动回到上一轮成功提交状态。`pause` 表示保留可恢复 Execution，用户可以稍后 `session.resume`。

## LLM 失败来源字段

模型调用可能发生在多个地方：

- 当前用户对话的父 Agent。
- 子 Agent 或工具内部。
- 后台上下文摘要。
- 后台长期记忆提取。

因此前端不应只显示“模型调用失败”。Core 会尽量提供：

| 字段 | 含义 | 示例 |
|---|---|---|
| `failure_source` | 报告失败的系统 | `agent_turn` |
| `failure_scope` | 失败影响范围 | `current_turn` |
| `failure_stage` | 失败阶段 | `parent_model_provider` / `context_summary` / `memory_extraction` |
| `user_action` | 推荐用户动作 | `revise_input_and_retry` / `resume_later` |

后台摘要和长期记忆失败属于维护任务，不应让当前对话失败。前端可通过 `session.status.maintenance.failed` 和 `maintenance.recent_failures[].job_type` 区分来源。

## 前端展示原则

- 展示 Core 提供的安全 `message`。
- 区分“会自动重试中”“已暂停可恢复”“已终止并自动恢复”。
- 显示 `request_id` 可用于排障，但不要显示 API key、完整 Provider body 或 traceback。
- 对已失效的流式草稿使用 stale/incomplete 标记，不把它当成最终回复。
