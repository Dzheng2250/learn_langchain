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
  -> HookDispatcher.PreToolUse
  -> ToolPolicyEngine
  -> HookDispatcher.PermissionRequest
  -> ApprovalService / LangGraph interrupt
  -> CapabilityEnforcer
  -> ToolExecutor
  -> HookDispatcher.PostToolUse
  -> Telemetry
```

`ToolRegistry` 只管理不可变元数据与 Agent audience，不负责授权。`ToolSpec` 必须声明 capability、approval、sandbox、network 和 timeout。`ObservedToolNode` 只作为 LangGraph 适配器，具体策略由 `ToolExecutionPipeline` 处理。

Tool 安全模块不再定义私有 Hook。它消费系统级 `PreToolUse`、`PermissionRequest` 和 `PostToolUse` 三个相位；完整模型、配置和失败策略见 [Agent 生命周期 Hook 架构](/docs/architecture/agent-lifecycle-hooks.md)。参数替换后必须重新执行 schema 与权限校验，Hook 不能绕过硬边界或创建永久授权。

## 工具异常隔离

`ToolExecutionPipeline` 是整条工具链的统一错误边界。上下文构造、Pre Hook、参数校验、命令规则解析、策略判断、审批、能力校验、工具实现、Post Hook 和 Telemetry 中出现的普通 `Exception`，都不能逃逸成 `graph_error`：管线会尽力记录失败，并返回 `status="error"` 的 `ToolMessage`，让模型决定修正参数、换用工具或向用户说明。

只有两类状态机信号允许继续上抛：

- `GraphBubbleUp`：包含 LangGraph `interrupt()`，用于持久审批暂停和恢复。
- `ToolBudgetExceeded`：用于终止超出 Execution 工具预算的调用。

命令解析器不支持某种合法 Bash 语法时，必须降级为“完整命令精确匹配、不可持久化”的规则，不能让解析异常终止 graph，也不能放宽为可复用前缀授权。工具已经成功产生副作用后，Post Hook 或 Telemetry 故障只记录诊断信息，不得把成功结果改写成失败，以免模型重试并重复执行副作用。

## 权限决策与审批

策略引擎只产生三种结果：

| 结果 | 含义 |
|---|---|
| `ALLOW` | 当前调用可继续，但仍必须通过 capability、路径、沙箱和网络硬边界。 |
| `ASK` | 创建持久审批请求并暂停 Execution，等待用户明确决定。 |
| `DENY` | 当前调用立即拒绝，不执行工具。 |

`ToolSpec.approval` 决定静态策略：`NONE` 不产生审批请求，`POLICY` 由持久规则和调用参数判断，`ALWAYS` 每次产生 `ASK`，`FORBIDDEN` 永远拒绝。策略引擎只返回 `ALLOW/ASK/DENY`，不再读取 UI 或全局审批开关。

### 可插拔审批模式

`ApprovalStrategyRegistry` 注册审批策略，`ApprovalModeResolver` 按“Session override -> 全局默认”解析有效模式，`ApprovalCoordinator` 统一创建请求、写审计和选择人工等待或自动响应。当前内置：

| 模式 | 对 `ASK` 的行为 | 是否创建长期规则 |
|---|---|---|
| `manual` | 持久化请求并通过 LangGraph `interrupt()` 等待用户 | 仅用户明确选择 Session/Workspace 响应时创建 |
| `accept_all` | 自动提交 `allow_once`，不暂停 Execution | 否 |

`accept_all` 不是“关闭安全”。stored deny、Hook `DENY/REJECT`、`FORBIDDEN`、主机执行禁用、路径/符号链接、沙箱、网络和 `CapabilityEnforcer` 仍优先执行。覆盖、移动、删除及 change set 应用产生的 `ASK` 也只获得本次允许，不会沉淀规则。新增 `deny_all`、`rules_only`、审批 Agent 或远程回调时，应注册新的 `ApprovalStrategy`，不能在 Tool pipeline 中增加模式分支。

全局模式来自 `LEARN_AGENT_TOOL_APPROVAL_MODE=manual|accept_all`；Session 可通过 `approval.mode.set` 持久覆盖或设为 `inherit`。未知持久值安全回退到 `manual` 并记录诊断事件。切换模式只影响之后创建的请求，已有 pending 继续使用请求创建时记录的 `approval_mode`。

审批响应分为六种：

| 响应 | 作用 |
|---|---|
| `allow_once` / `deny_once` | 只处理当前 `tool_call_id`，不创建规则。 |
| `allow_session` / `deny_session` | 为当前 Workspace 和 Session 保存规则。 |
| `allow_workspace` / `deny_workspace` | 为当前 Workspace 保存跨 Session 规则。 |

只有 `persistable=true` 的请求才能选择 Session 或 Workspace 范围。简单命令保存解析后的 argv 前缀规则，因此可覆盖同前缀的后续调用。能够被 Bash parser 完整解析的管道、重定向和命令连接等复合调用保存为 SHA-256 精确规则：规则不包含命令正文，并且只匹配完整内容相同的调用。无法可靠解析的 shell 语法仍只能单次处理。无论采用哪种规则，每次执行都要重新经过 Hook、Policy 与 Capability Enforcer；显式 deny 的优先级高于 allow。

## 暂停与恢复闭环

`ASK` 会依次执行：

```text
创建 tool_approval_requests 行
  -> LangGraph interrupt()
  -> 保存 checkpoint
  -> 发送 tool_approval_required
  -> Execution 以 stop_reason=tool_approval 暂停
  -> 用户提交 approval.resolve
  -> Command(resume=...) 回到同一工具调用
  -> 再次校验策略和硬边界
  -> 执行或拒绝工具
```

审批恢复不会重新开始整个 Turn，也不会重放已经成功完成的工具。`request_id` 与 `execution_id + tool_call_id` 绑定，只能成功处理一次。普通 `session.resume` 不能代替 `approval.resolve`，因为恢复工具中断必须携带具体审批响应。

交互式 `learn-agent chat` 会在当前终端直接显示工具、原因和 capability，并询问审批选择。一次性 chat、脚本或 daemon 重启后的请求使用 `learn-agent approval list/resolve`；待审批状态保存在 SQLite 和 checkpoint 中，不依赖原终端存活。

## 审批数据

- `tool_approval_requests`：待处理或已处理的请求事实，只保存脱敏、截断参数摘要。
- `tool_permission_rules`：Session 或 Workspace 作用域的 allow/deny 规则。
- `tool_approval_audit`：最终响应审计；`decision_source` 区分 `user/automatic/hook/legacy`，`approval_mode` 记录当时模式；重复 resolve 不会生成第二条记录。
- LangGraph checkpoint：保存 Agent 暂停位置，不能由审批表替代。

Session 归档保留这些记录；hard delete 才会清理 Session 关联请求、规则和审计。Workspace 级规则不属于单个 Session，不应随普通 Session 删除而消失。

## 不可绕过边界

- Workspace 路径拒绝绝对路径、目录穿越和符号链接逃逸。
- 容器命令默认只读、禁网，并保留 CPU、内存、PID、超时和输出上限。
- 主机完全访问默认禁用，不能被持久 allow 规则静默放行。
- 网络能力单独声明，不随文件或命令审批自动获得。
- 审批表只保存脱敏参数摘要；Trace、Telemetry 与正式历史不得保存密钥。

## 扩展要求

新增工具必须通过 registry、policy、path/network、approval、audience 和敏感参数测试。不得把未经注册的 LangChain tool 直接注入图中，也不得把 Hook 当作权限授予接口。

## 待审批生命周期

审批等待是持久化暂停，不设置自动超时：用户可能在 daemon 重启后继续处理同一 Execution。`approval.list` 只返回仍绑定可恢复 Execution 的请求；用户不再继续时，应通过 `session discard` 或 Session 删除显式结束。Session 硬删除会经 Execution 外键级联清理审批请求和审计记录。

同一 `execution_id + tool_call_id` 的请求会在 checkpoint 重放时复用，不能删除 `create_request()` 的幂等查询。审批响应只能成功提交一次；并发或重复响应不会追加第二条审计记录。

网络策略仅接受 `deny`、`allowlist`、`allow`。未知配置启动即失败，不能按“非 deny 即允许”处理。

## 资源活动与权限的边界

权限管线决定 Tool 是否可以执行；资源活动账本记录执行后实际观察到的读取和变更。
`PreToolUse` 收到只读 `resource_evidence`，`PostToolUse` 收到 `resource_activity_ids`。
账本失败不会改变已经完成的 Tool 结果，但会产生资源活动记录失败遥测用于诊断。
