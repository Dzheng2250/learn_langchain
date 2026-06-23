# 通用 LLM 错误恢复设计

> 文档状态：Current
> 权威范围：LLM 错误事实提取、分类、重试、草稿失效和恢复语义
> 维护触发：模型 Provider、错误 Adapter、重试预算、流式草稿或 Execution 恢复行为变化

## 本文负责

- 说明为什么不能只按 HTTP 状态码判断错误。
- 说明 Parser、Adapter、Policy 和 Retry Executor 的职责边界。
- 说明前台与后台模型调用的重试预算差异。
- 说明流式草稿失效后前端和消息历史应该如何处理。

## 本文不负责

- 不定义公开 RPC 字段；见 [RPC 参考](/docs/api/rpc-reference.md)。
- 不定义前端完整渲染规范；见 [流式事件参考](/docs/api/streaming-events.md)。
- 不实现模型自动切换、Token 刷新或上下文自动压缩重放。

## 优化目标

外部模型 API 失败不应该直接把 Session 卡死。系统需要区分：

- 临时失败：限流、超时、网络中断、服务商过载，可以短时间重试。
- 永久失败：内容审查、认证、无效请求、模型不存在，重发同一请求仍会失败。
- 后台失败：摘要和长期记忆失败不应该影响前台对话。

最终链路是：

```text
原始异常
  -> 通用事实提取
  -> 错误语义分类
  -> 恢复策略决策
  -> 有界重试
  -> 成功继续 / 失败后安全暂停或终止
```

## 为什么不能只看 HTTP 状态码

同样是 `429`，可能表示短期限流，也可能表示月度额度耗尽。同样是 `400`，可能是无效参数，也可能是内容审查拒绝。只按状态码会导致两类问题：

- 把不可恢复错误反复重试，浪费时间和额度。
- 把可恢复错误直接终止，用户体验差。

因此 Core 会同时读取 HTTP status、headers、结构化 body、SDK 异常类型和受限长度的错误文本。

## 组件职责

| 组件 | 职责 | 不做什么 |
|---|---|---|
| `GenericProviderErrorParser` | 从异常链提取状态码、headers、body、code、request id、Retry-After 和通用类别。 | 不决定 pause/terminate。 |
| `ProviderErrorAdapter` | 为特定服务商补充别名或修正 code 映射。 | 不覆盖核心策略，不泄漏原始 body。 |
| `ErrorResolutionPolicy` | 把类别转换为 retryable、action 和用户可见安全消息。 | 不调用模型。 |
| `ResilientModelProvider` | 使用 tenacity 执行有界重试并发送重试事件。 | 不重放工具，不创建新 Execution。 |

## 重试策略

Core 显式依赖 `tenacity`，不手写复杂退避循环。`ChatOpenAI.max_retries` 被设置为 `0`，避免 SDK 隐式重试和 Core 重试叠加。

默认预算：

| LLM 用途 | 最大尝试 | 最大等待 |
|---|---:|---:|
| `parent_agent` | 3 | 30 秒 |
| `subagent` | 3 | 30 秒 |
| `file_summary` | 3 | 30 秒 |
| `context_summary` | 2 | 15 秒 |
| `memory_extraction` | 2 | 15 秒 |

等待优先级：

```text
Retry-After Header
  -> 响应体 retry_after / resets_at
  -> 错误文本中的 try again in
  -> 指数退避 + 随机抖动
```

自动重试的类别：`rate_limited`、`service_overloaded`、`service_unavailable`、`timeout`、`connection_failed`、`stream_interrupted`。

不自动重试的类别：`content_rejected`、`authentication`、`quota_exhausted`、`usage_limit`、`invalid_request`、`model_not_found`、`context_length_exceeded`。

## 流式草稿失效

一次模型尝试可能已经向前端输出了部分 token，然后网络中断或服务端错误。此时不能把已经显示的草稿当成最终回答，也不应该悄悄删除。

Core 的处理：

1. 每次 LLM 尝试生成独立 `attempt_id`。
2. token 事件携带可选 `attempt_id`。
3. 若该尝试已经输出 token 且将进入重试，发送 `model_attempt_invalidated`。
4. 前端将旧草稿标记为 stale/incomplete。
5. 成功尝试产生的最终 `AIMessage` 才能提交到 Session。

## Execution 恢复语义

| 场景 | 行为 |
|---|---|
| 重试成功 | 当前 Slice 正常继续。 |
| 临时错误耗尽 | Execution 暂停，保留 checkpoint，可 `session.resume`。 |
| 内容审查或无效请求 | 失败 Turn 终止且不保存，Session 回到上一轮成功提交状态。 |
| 后台摘要/记忆失败 | 不影响当前对话，由 Maintenance Queue 记录并按任务级策略重试。 |

## 如何增加服务商 Adapter

新增 Adapter 时只补充服务商别名，不要改变核心策略：

```python
class ExampleProviderAdapter:
    def enrich(self, envelope, exc):
        if envelope.provider_code == "provider_busy":
            return replace(envelope, category=ErrorCategory.SERVICE_OVERLOADED)
        return envelope
```

接入位置应在组合根或错误处理工厂中注册。Adapter 异常必须被隔离，不能覆盖原始错误事实。

## 当前不支持

- 不自动切换模型或服务商。
- 不自动刷新认证令牌。
- 不重放已经成功执行的工具。
- 不因上下文超限自动触发强制压缩并重放当前请求。
- 不保存完整 Prompt、模型响应或 Provider 原始错误 body。
