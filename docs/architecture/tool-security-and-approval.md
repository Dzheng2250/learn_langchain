# Tool 安全、审批与 Hook 架构

> 文档状态：Current
> 权威范围：工具注册、Hook、权限决策、审批恢复与强制执行边界
> 维护触发：新增工具能力、审批响应、沙箱模式、网络策略或 ToolNode 执行流程

## 本文负责

本文定义所有 Agent 工具从注册到执行必须经过的统一安全边界。

## 本文不负责

- 不定义具体工具的业务功能和参数；见工具扩展指南。
- 不解释 Agent 循环、Execution 与 checkpoint 的完整生命周期。
- 不把审批记录作为任务恢复或合规审计的替代品。

## 执行链

```text
ToolRegistry
  -> PreToolHook
  -> ToolPolicyEngine
  -> ApprovalService / LangGraph interrupt
  -> CapabilityEnforcer
  -> ToolExecutor
  -> PostToolHook / Telemetry
```

`ToolRegistry` 只管理不可变元数据与 Agent audience，不负责授权。`ToolSpec` 必须声明 capability、approval、sandbox、network 和 timeout。`ObservedToolNode` 只作为 LangGraph 适配器，具体策略由 `ToolExecutionPipeline` 处理。

Hook 是可信的进程内扩展点：可以规范参数或拒绝调用，但不能主动授予权限。参数替换后必须重新执行 schema 与权限校验。pre-hook 失败采用 fail-closed；post/error hook 失败只记录，不覆盖工具结果。

## 审批与恢复

`ALLOW / ASK / DENY` 是策略结果。用户可选择单次、Session 或 Workspace 范围的 allow/deny。`ASK` 使用 LangGraph `interrupt()` 保存 checkpoint，`approval.resolve` 使用 `Command(resume=...)` 恢复同一工具调用。恢复后再次运行硬边界检查。

## 不可绕过边界

- Workspace 路径拒绝绝对路径、目录穿越和符号链接逃逸。
- 容器命令默认只读、禁网，并保留 CPU、内存、PID、超时和输出上限。
- 主机完全访问默认禁用，不能被持久 allow 规则静默放行。
- 网络能力单独声明，不随文件或命令审批自动获得。
- 审批表只保存脱敏参数摘要；Trace、Telemetry 与正式历史不得保存密钥。

## 扩展要求

新增工具必须通过 registry、policy、path/network、approval、audience 和敏感参数测试。不得把未经注册的 LangChain tool 直接注入图中，也不得把 Hook 当作权限授予接口。
