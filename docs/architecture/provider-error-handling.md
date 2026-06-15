# 模型服务商错误处理设计

## 1. 为什么需要单独的错误处理层

模型调用失败并不都代表同一种问题：

- 内容审查拒绝或请求参数错误：再次发送相同请求仍会失败。
- 限流、网络中断或服务暂时不可用：稍后恢复可能成功。
- 未知异常：系统无法安全判断，应该保留执行现场供排查。

旧实现把这些异常统一标记为 `paused_error`。这会导致内容审查拒绝后的
Execution 一直绑定 Session，用户无法继续普通对话，只能手动执行
`session discard`。

新实现先把服务商异常转换为稳定的内部分类，再由独立策略决定如何处理。
Agent Service 不需要知道阿里云、OpenAI 或其他服务商的具体错误格式。

## 2. 三层结构

```text
服务商异常
    |
    v
ProviderErrorParserRegistry
    |  提取服务商、HTTP 状态码、错误码和通用类别
    v
ErrorResolutionPolicy
    |  决定暂停还是终止，并生成可公开提示
    v
AgentTurnService
       pause     -> 保留 pending Execution，允许 resume
       terminate -> 解除 Session 绑定，清理 checkpoint，允许下一轮聊天
```

### ProviderErrorParser

Parser 只负责理解某个服务商的错误格式，不决定业务行为。

例如 `AliyunErrorParser` 将：

```text
data_inspection_failed
```

转换为：

```text
ErrorCategory.CONTENT_REJECTED
```

当前注册顺序为：

1. `AliyunErrorParser`：识别阿里云 Model Studio 特有错误码。
2. `OpenAICompatibleErrorParser`：按通用 HTTP 状态码分类。
3. 无 Parser 识别时返回 `UNKNOWN`。

更换服务商时，应新增 Parser 并注册到 `ProviderErrorParserRegistry`，而不是修改
Agent 循环。

### ErrorResolutionPolicy

Policy 根据内部类别选择处置方式。默认策略为：

| 类别 | 是否可重试 | 处置 |
|---|---:|---|
| `content_rejected` | 否 | 终止本轮并解除 Session |
| `invalid_request` | 否 | 终止本轮并解除 Session |
| `authentication` | 否 | 终止本轮，提示修改配置 |
| `rate_limited` | 是 | 暂停，允许稍后恢复 |
| `service_unavailable` | 是 | 暂停，允许稍后恢复 |
| `network` | 是 | 暂停，允许稍后恢复 |
| `unknown` | 是 | 保守暂停，等待人工判断 |

Parser 与 Policy 分离的原因是：服务商负责定义错误格式，但“遇到该错误后系统
应该做什么”属于本项目自己的业务决策。

## 3. 不可重试错误如何终止

`ExecutionRepository.terminate()` 在一个 SQLite 事务中完成：

1. 将 Execution 标记为 `discarded`。
2. 保存稳定的停止原因，例如 `content_rejected`。
3. 将 `original_input` 替换为 `[REDACTED]`。
4. 将 checkpoint 标记为 `cleanup_pending`。
5. 清除 Session 的 `pending_execution_id`。

事务成功后，Agent Service 将 checkpoint 清理任务加入后台维护队列。即使清理
任务暂时失败，Session 也已经解除阻塞，用户可以继续对话。

目前复用 `discarded` 状态表示不可重试终止，避免为了增加状态值而重建已有
SQLite CHECK 约束。停止原因仍可区分用户主动 discard 与服务商拒绝。

## 4. 安全边界

发送给 CLI 和遥测系统的是经过清理的公开错误信息，不发送完整服务商响应。
完整响应可能包含：

- 被拒绝内容的片段。
- 请求 ID。
- 服务商内部信息。

Parser 可以读取原始异常用于分类，但结构化结果只保留：

```text
provider
provider_code
http_status
error_category
retryable
error_action
```

## 5. 如何接入新服务商

实现一个 Parser：

```python
class NewProviderParser:
    def parse(self, exc):
        if not recognizes(exc):
            return None
        return ParsedProviderError(
            category=ErrorCategory.RATE_LIMITED,
            provider="new_provider",
            provider_code="quota_busy",
            http_status=429,
        )
```

然后在组合根中构造 Registry：

```python
handler = ProviderErrorHandler(
    ProviderErrorParserRegistry(
        [NewProviderParser(), OpenAICompatibleErrorParser()]
    )
)
```

若项目需要改变处置方式，例如对某种限流错误自动终止，应提供新的
`ErrorResolutionPolicy`，不应修改 Parser。

## 6. 当前边界

- 当前不会自动重试临时错误，只会暂停并允许显式 `session resume`。
- 未知错误采用保守暂停策略。
- 不可重试错误会脱敏 `executions.original_input`，但已经在更早成功 Turn 中
  持久化的历史消息、摘要或长期记忆不会被自动删除。
- 若服务商因已有 Session 上下文持续拒绝请求，仍需要后续实现显式的
  `session reset-context` 功能。
