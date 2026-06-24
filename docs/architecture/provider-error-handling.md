# 模型服务商错误处理设计

> 文档状态：Current
> 权威范围：Provider 错误解析、分类、策略选择和 Session/Execution 处置
> 维护触发：Provider、错误模型、Parser Registry、重试策略或恢复语义变化

## 本文负责

- 说明 Provider 异常如何被转换为稳定内部分类。
- 说明哪些错误终止 Turn，哪些错误暂停 Execution。
- 指向自动重试链路的详细设计。

## 本文不负责

- 不定义客户端显示字段；见 [错误与恢复参考](/docs/api/error-reference.md)。
- 不定义流式重试事件；见 [流式事件参考](/docs/api/streaming-events.md)。

## 当前结构

```text
原始异常
  -> ProviderErrorParserRegistry
  -> ErrorResolutionPolicy
  -> ResilientModelProvider / AgentTurnService
  -> retry / pause / terminate
```

Parser 只提取事实，例如 HTTP 状态码、Provider code、request id、Retry-After。Policy 才决定业务动作。这样更换服务商时只需要补充 Adapter，不需要改 Agent 主循环。

## 自动重试

当前已经支持 Provider 中立的模型级自动重试。详细说明见 [通用 LLM 错误恢复](/docs/architecture/provider-error-recovery.md)。

重试只包裹当前 LLM 调用，不重放已经成功执行的工具。SDK 内置重试被关闭，由 Core 统一控制预算和前端事件。

## 失败处置

| 结果 | Session/Execution 语义 |
|---|---|
| 重试成功 | 当前 Slice 继续执行，不创建新 Execution。 |
| 临时错误耗尽 | Execution 暂停，保留 checkpoint，用户稍后 resume。 |
| 确定性错误 | 失败 Turn 不写入正式历史，Session 回到上一轮成功提交状态。 |
| 后台维护失败 | 当前对话不失败，由 Maintenance Queue 持久化重试或暴露状态。 |

## 安全边界

Trace、Telemetry 和前端事件只保存安全摘要：错误类别、HTTP 状态、Provider code、request id、attempt、delay 和 purpose。完整 Prompt、模型响应、工具结果、API key 和 `.env` 内容不得进入这些记录。
