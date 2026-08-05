# 内置 Tool 使用与维护手册

> 文档状态：Current
> 权威范围：当前内置 Tool 的用途、注册条件、执行边界与维护入口
> 维护触发：新增、删除、重命名 Tool，或修改 audience、能力、审批、沙箱、参数与配置开关

本文回答“项目现在有哪些 Tool、为什么 Agent 能看到它、调用会经过什么、修改后应测试哪里”。新增 Tool 的步骤见[新增工具指南](/docs/development/tool-extension-guide.md)，权限模型见[Tool 安全与审批](/docs/architecture/tool-security-and-approval.md)。

## 本文负责

- 维护当前内置 Tool 的可见角色、注册条件、权限属性和实现位置。
- 说明已有 Tool 的使用顺序、排查入口和回归测试。

## 本文不负责

- 不重新定义 Tool 安全与审批规则；以架构文档为准。
- 不替代新增 Tool 的实现教程；以扩展指南为准。
- 不定义 CLI、TUI 或流式事件协议；以对应 API 文档为准。

## 1. 运行链路

```text
WorkspaceRuntimeFactory
  -> create_workspace_toolset()
  -> ToolRegistry.register(ToolSpec)
  -> 分离 model_tools_for / execution_tools_for 并 freeze
  -> Agent/LedgerBackedToolNode
  -> ToolExecutionPipeline
  -> Hook -> Policy/Approval -> CapabilityEnforcer
  -> Durable Tool Ledger -> Tool
```

核心维护入口：

- `src/core/tools/catalog.py`：`ToolSpec`、能力、风险、审批、沙箱和 audience 定义。
- `src/core/tools/registry.py`：唯一的内置 Tool 组合与注册入口。
- `src/core/tools/security/`：策略、审批、路径/网络硬限制与执行管线。
- `src/core/workspace/runtime.py`：把配置和 Workspace 依赖传入 Toolset。
- `src/cli/render.py`、`src/tui/renderer.py`：调用、结果和审批的安全展示。

每个内置工具还必须正确声明：

- `effect`：只读、内部状态变更、Workspace 变更或外部副作用。
- `replay_policy`：安全重试、结果重放、状态对账或人工恢复。
- `parallel_safe`：仅无审批的纯读取工具可以设为 `true`。

这些字段决定 checkpoint 粒度和崩溃恢复行为，不只是展示元数据。声明冲突会在 Registry
构建时直接失败。

## 2. 当前工具目录

| Tool | 可见角色 | 用途 | 审批/启用条件 | 实现 |
|---|---|---|---|---|
| `get_weather` | Parent、Subagent | 查询示例天气数据 | 无审批 | `tools/weather.py` |
| `read_workspace_file` | Subagent | 按行读取文件片段 | 只读硬边界 | `tools/workspace.py` |
| `read_entire_file` | Subagent | 在大小限制内读取完整文件 | 只读硬边界 | `tools/workspace.py` |
| `read_workspace_file_lite` | Parent | 轻量读取文件片段 | 只读硬边界 | `tools/workspace.py` |
| `list_skills`、`read_skill` | 两者 | 枚举、读取本地 Skill | 无审批 | `tools/skills.py` |
| `summarize_large_file` | Subagent | 分块并行摘要大文件 | 会调用 FILE_SUMMARY LLM | `tools/summarization.py` |
| `run_command_in_container` | Parent | 在只读 Workspace 副本中运行命令 | POLICY；Docker、禁网 | `tools/commands.py` |
| `write_workspace_file` | Parent | 新建或显式覆盖文本文件 | POLICY；覆盖必须重新审批 | `tools/workspace_write.py` |
| `apply_workspace_patch` | Parent | 基于同一快照更新已有文本文件的多个 hunk | POLICY；模型可见 | `tools/workspace_patch.py`、`tools/workspace_write.py` |
| `replace_workspace_text` | Parent | 恢复旧 checkpoint 的精确替换 | POLICY；仅执行兼容，不再对模型可见 | `tools/workspace_write.py` |
| `create_workspace_directory` | Parent | 创建目录 | POLICY | `tools/workspace_write.py` |
| `move_workspace_path` | Parent | 移动文件或目录 | 每次审批 | `tools/workspace_write.py` |
| `delete_workspace_path` | Parent | 删除文件或显式递归删除目录 | 每次审批 | `tools/workspace_write.py` |
| `stage_command_changes` | Parent | 在可写临时副本中执行命令并生成变更集 | POLICY；默认关闭 | `tools/command_changes.py` |
| `apply_staged_changes` | Parent | 校验指纹、哈希后应用变更集 | ALWAYS；默认关闭 | `tools/command_changes.py` |
| `discard_staged_changes` | Parent | 丢弃未应用变更集 | 默认关闭 | `tools/command_changes.py` |
| `task_plan`、`task_update`、`task_list`、`task_get` | Parent | 管理当前 Execution 私有任务 | 仅注入 `task_service` 时注册 | `core/tasks/tools.py` |
| `delegate_to_subagent` | Parent | 委派有步数和上下文限制的只读研究 | 不递归委派 | `core/subagent/graph.py` |

写工具由 `LEARN_AGENT_FILE_WRITE_ENABLED` 控制；staged command 工具由 `LEARN_AGENT_COMMAND_WRITE_ENABLED` 控制。路径必须是 Workspace 相对路径，且 `.git`、`.env*`、运行时数据库和敏感目录始终不可访问。批准不会绕过这些硬边界。

同一已有文件的多处编辑必须合并为一次 `apply_workspace_patch`；协议、快照匹配、回滚和恢复规则见 [Workspace 编辑架构](/docs/architecture/workspace-editing.md)。

## 3. 如何使用与排查

- Tool 是否可见：先检查 `registry.py` 的 audience 和配置开关，再检查 Runtime 是否重新创建。
- 调用要求审批：CLI/TUI 处理 `tool_approval_required`；批准后从 LangGraph checkpoint 恢复，不会另起一次调用。
- Tool 返回错误：先查 `tool_call_start/result` 与 Telemetry，再区分策略拒绝、能力校验、超时和实现异常。
- 修改参数：同步更新函数签名、模型可见 schema、CLI/TUI 脱敏展示和审批规则身份。

## 4. 命令变更集工具

这组工具用于让命令安全地修改 Workspace，仅在以下配置启用时注册：

```env
LEARN_AGENT_COMMAND_WRITE_ENABLED=true
```

它们只对 Parent Agent 可见，并组成不可跳步的两阶段流程：

```text
stage_command_changes
  -> 审查命令输出、文件清单和 Approval fingerprint
  -> apply_staged_changes 或 discard_staged_changes
```

### `stage_command_changes(command)`

把真实 Workspace 复制到临时目录，在隔离、禁网的 Docker 容器中执行命令，并比较执行前后的文件。它不会修改真实 Workspace，适用于格式化、代码生成、批量自动修复等可能写文件的命令。

返回内容包括：

- `change_set_id`：后续应用或丢弃时使用。
- `Changes`：新增、修改和删除的文件清单。
- `Approval fingerprint`：形如 `modify:src/app.py` 的稳定清单。
- 命令退出码和受长度限制的输出。

变更集保存在运行时目录，有效期为 24 小时，并受文件数量和总字节数限制。

### `apply_staged_changes(change_set_id, expected_changes)`

将已审查的变更集应用到真实 Workspace。`expected_changes` 必须原样使用 `stage_command_changes` 返回的完整 `Approval fingerprint`，否则拒绝执行。

该操作始终需要单次审批。应用前还会检查：

- 变更集属于当前 Workspace 且未过期。
- 真实文件自暂存后未被用户或其他进程修改。
- 暂存文件的大小和 SHA-256 未被篡改。
- 每个目标路径仍在 Workspace 内且不属于敏感路径。

写入使用原子替换；多文件应用中途失败时会利用备份回滚。

### `discard_staged_changes(change_set_id)`

删除指定临时变更集，不修改真实 Workspace。用户拒绝变更或不再需要暂存结果时使用。

### 与 `run_command_in_container` 的区别

`run_command_in_container` 只在只读 Workspace 副本中运行命令，产生的文件修改不会回写；变更集工具允许命令修改临时副本，但只有经过审查并调用 `apply_staged_changes` 后才会改变真实 Workspace。项目不会把真实 Workspace 直接以可写方式挂载到命令容器。

## 5. 维护检查

| 修改类型 | 必查位置 | 重点测试 |
|---|---|---|
| 注册、角色或排序 | `tools/registry.py`、`tools/catalog.py` | `test_agent_execution_architecture.py` |
| 权限、审批、Hook | `tools/security/`、SQLite approval adapter | `test_tool_security.py`、`test_capability_enforcer.py` |
| 文件/命令工具 | `workspace*.py`、`commands.py`、`command_changes.py` | `test_workspace_write.py` |
| Task 工具 | `core/tasks/tools.py`、Task service | `test_private_tasks.py` |
| 前端展示 | `cli/render.py`、`tui/renderer.py` | `test_cli_render.py`、`test_tui_chat_log.py` |

提交前至少运行：

```powershell
python -B -m unittest tests.unit.test_tool_security tests.unit.test_workspace_write -v
python -B -m unittest discover -s tests -v
git diff --check
```

新增、删除或重命名 Tool 时，必须同步更新本文工具目录；涉及外部事件、配置或审批交互时，还要同步对应 API、配置和架构文档。

## 6. 资源活动观测

所有内置 Tool 通过 `ResourceActivityRecorder` 上报统一事实。文件读取和结构化写入是 `exact/range/summary`；
容器命令无法证明内部实际打开的文件，因此只报告 `scope_only`。Hook 可消费 `resource_activity_ids`，但不能修改权威账本。
维护 Tool 时必须同步声明其观测精度，并验证 `resource_activity.summary/list` 的结果。
