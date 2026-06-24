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
| `/session <name>` | 切换 session | `/session feature-x` |
| `/session` | 查看当前 session | `/session` |
| `/clear` | 清空日志 | `/clear` |
| `Ctrl+O` | 展开或收起工具调用明细；任务进度块始终可见 | — |
| `Ctrl+O` | 展开或收起工具调用过程；任务进度块始终保留最新状态 | — |
| `Ctrl+C` | 取消操作 | — |
| `Ctrl+D` | 退出 TUI | — |

## 5. 当前支持的能力

### 连接与启动

- 自动加载 `runtime_dir` 下的 auth token。
- 启动时自动发现 workspace 根目录。
- 自动 ping Core daemon 验证连接。
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
- 显示暂停 execution 的恢复提示。
- `/resume` 恢复暂停 execution。
- `/discard` 丢弃暂停 execution。

### Goal 模式

- 通过 `/goal` 进入目标模式，状态栏显示 `goal` 标记。
- 目标完成后显示 `★ goal completed` 标记。

### 状态展示

- 连接状态、daemon 地址、session 名称。
- 上下文使用额度 `ctx: XK/128K (X%)`。

## 6. 当前不支持

### 协议与通信

- 执行中取消协议（无 `cancel` RPC）。
- 断线后事件续传。
- 同一连接并行请求。
- 自动重连。
- TLS 或远程连接。

### UI 功能

- Session 列表与历史查询。
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

