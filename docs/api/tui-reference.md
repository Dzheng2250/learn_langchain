# TUI 使用与命令参考

> 文档状态：Current
> 权威范围：内置 TUI 的用户命令、当前能力和使用限制
> 维护触发：TUI 命令、快捷键、用户可见能力或限制变化

本文面向内置 TUI 使用者。第三方前端如何实现 Core 客户端见
[前端接入指南](/docs/api/tui-client-guide.md)。

## 本文负责

- TUI 的 `/` 命令和快捷键。
- TUI 当前用户可见能力与限制。

## 本文不负责

- 不解释 TUI 组件实现；见 [TUI 架构](/docs/architecture/tui-architecture.md)。
- 不定义底层 RPC 和流式事件字段；见 [RPC 参考](/docs/api/rpc-reference.md) 和
  [流式事件参考](/docs/api/streaming-events.md)。

## 4. 命令参考

| 命令 | 用途 | 示例 |
|---|---|---|
| `/help` | 显示帮助 | `/help` |
| `/goal <msg>` | 以目标模式发送消息 | `/goal 分析项目结构` |
| `/resume` | 恢复暂停的 execution | `/resume` |
| `/resume <指令>` | 带额外指令恢复 | `/resume 先只改测试` |
| `/discard` | 丢弃暂停的 execution | `/discard` |
| `/approvals` | 列出当前 Session 的待审批请求 | `/approvals` |
| `/approve [id] <response>` | 处理待审批请求并恢复原 Execution | `/approve allow_once` |
| `/approval-mode` | 查看当前审批模式 | `/approval-mode` |
| `/approval-mode <mode>` | 设置 Session override 或恢复继承 | `/approval-mode accept_all --ack` |
| `/session <name>` | 切换 session | `/session feature-x` |
| `/session` | 查看当前 session | `/session` |
| `/clear` | 清空日志 | `/clear` |
| `Ctrl+O` | 展开或收起工具调用过程；任务进度块始终保留最新状态 | — |
| `Ctrl+T` | 展开或收起最近的 thinking 块 | — |
| `Ctrl+Y` | 打开工具审批中心 | — |
| `Ctrl+C` | 取消操作 | — |
| `Ctrl+D` | 退出 TUI | — |

## 5. 当前支持的能力

### 连接与启动

- 自动加载 `runtime_dir` 下的 auth token。
- 启动时自动发现 workspace 根目录。
- 自动 ping Core daemon 验证连接。
- 启动和重新进入时自动加载当前 Session 最近 30 个完整 Turn。
- 启动时自动检查 session 是否有暂停的 execution。
- 状态栏实时反映连接状态（绿/黄/红）。

### 流式展示

- 每个 token chunk 到达后立即展示，不等待完整回复。
- 工具调用明细默认折叠，可用 `Ctrl+O` 展开或收起。
- goal 模式下的 task 工具结果会更新一个“最新任务进度”状态块，而不是每次更新都追加一份完整清单。
- 敏感参数脱敏（`api_key`、`token`、`password` → `[REDACTED]`）。
- 长内容截断（参数 240 字符/字段，列表 20 项，深度 20 层）。

### Session 管理

- 通过 `/session` 切换 session 名称。
- 切换后清空旧视图并加载目标 Session 历史、上下文额度、暂停状态和审批模式；Agent 请求执行期间禁止切换。
- 历史 reasoning 和工具过程默认折叠，继续分别由 `Ctrl+T` 和 `Ctrl+O` 控制。
- 向上滚动到已加载内容顶部时自动读取更早的完整 Turn；前插历史后保持当前可视位置。
- 显示暂停 execution 的恢复提示。
- `/resume` 恢复暂停 execution。
- `/discard` 丢弃暂停 execution。

### 工具审批

- `manual` 模式下，Core 确认 Execution 已以 `tool_approval` 暂停后，TUI 才在聊天日志与输入框之间显示内嵌审批条，避免 checkpoint 尚未提交时提前恢复。
- 审批条不会遮挡历史内容，以紧凑操作项提供三个高频动作：`A` 允许一次、`S` 在当前 Session 始终允许、`D` 拒绝一次。可可靠解析的复合命令可保存只匹配相同完整调用的 Session 规则；`persistable=false` 时隐藏 Session 允许项。
- Workspace 级授权和持久拒绝等低频高级操作仍可通过 `/approve <request_id> <response>` 完成，不挤占日常审批界面。
- `Ctrl+Y` 打开审批中心，可查看完整 pending 队列并切换 `inherit/manual/accept_all`。启用 `accept_all` 必须再确认一次；既有 pending 不会自动执行。
- 状态栏显示 `approval: manual` 或 `approval: auto`。自动模式不会显示新审批条，但拒绝规则和路径、沙箱、网络硬边界仍会生效。
- `/approve`、`/approvals` 和 `/approval-mode` 保留为无鼠标、脚本调试和 UI 故障恢复入口。

### Goal 模式

- 通过 `/goal` 进入目标模式，状态栏显示 `goal` 标记。
- 目标完成后显示 `★ goal completed` 标记。

### 状态展示

- 连接状态、daemon 地址、session 名称。
- 上下文使用额度 `ctx: XK/192K (X%)`；分母来自 `LEARN_AGENT_MODEL_CONTEXT_LIMIT`。
- 有效工具审批模式 `approval: manual/auto`。

## 6. 当前不支持

### 协议与通信

- 执行中取消协议（无 `cancel` RPC）。
- 断线后事件续传。
- 同一连接并行请求。
- 自动重连。
- TLS 或远程连接。

### UI 功能

- Session 列表选择器。
- 消息编辑或删除。
- Markdown / 代码渲染。
- 文件上传或图片展示。
- 多窗口或分屏。
- 搜索和过滤日志。
- 配置页或设置界面。

### Daemon 管理

- 从 TUI 启动/停止 daemon。
- 查看 daemon 日志。
- Trace/Telemetry 查看。
