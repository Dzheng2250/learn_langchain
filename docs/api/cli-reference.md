# CLI 命令参考

> 文档状态：Current
> 权威范围：`learn-agent` 用户命令与 `learn-agent-core` 管理命令
> 维护触发：新增或修改 CLI 命令、参数、输出行为

## 本文负责

- `learn-agent` 与 `learn-agent-core` 的命令、参数和用户可见行为。
- 命令失败时的退出语义和使用示例。

## 本文不负责

- 不解释 CLI 内部模块和 daemon 管理实现；见 CLI 架构。
- 不定义底层 RPC 字段；见 IPC 和 RPC 参考。


## 命令总览

| 命令 | 用途 | 是否要求 daemon 运行 |
|---|---|---:|
| `learn-agent start` | 启动用户级 Core daemon | 否 |
| `learn-agent stop` | 请求 Core 优雅关闭 | 是 |
| `learn-agent status` | 检查 daemon 状态 | 否 |
| `learn-agent chat` | 交互式或单次 Agent 对话 | 是 |
| `learn-agent tui` | 启动 TUI 终端界面 | 是 |
| `learn-agent session status` | 查询 Session 和待恢复任务 | 是 |
| `learn-agent session resume` | 恢复待执行任务 | 是 |
| `learn-agent session discard` | 丢弃待恢复任务 | 是 |
| `learn-agent session reset` | 从归档消息重建 Session 缓存 | 是 |
| `learn-agent session delete` | 归档或硬删除 Session | 是 |
| `learn-agent approval list` | 查询待审批工具调用 | 是 |
| `learn-agent approval resolve` | 审批并恢复工具调用 | 是 |
| `learn-agent hooks path` | 查看 Hook 配置文件搜索路径 | 否 |
| `learn-agent hooks init` | 生成用户级 `hooks.json` 模板 | 否 |
| `learn-agent hooks validate` | 校验当前 Hook 配置文件 | 否 |
| `learn-agent trace` | 查询本地系统 Trace | 否 |

`learn-agent-core` 是管理与调试入口，不用于日常对话：

| 命令 | 用途 | daemon 必须停止 |
|---|---|---:|
| `learn-agent-core serve` | 前台运行 Core daemon，便于调试 | 是 |
| `learn-agent-core init-user-config` | 将指定 `.env` 复制到用户级配置目录 | 否；配置生效需要重启 |
| `learn-agent-core migrate-workspace` | 迁移旧 PostgreSQL Workspace Schema | 是 |
| `learn-agent-core migrate-local-state` | 将保留 Session 导入本地权威状态 | 是 |
| `learn-agent-core rollback-local-state` | 回滚明确支持的本地 Schema 版本 | 是 |
| `learn-agent-core gc-artifacts` | 删除未被引用的本地 Artifact | 建议停止 |

## Workspace 与 Session 参数

- `--workspace <path>`：显式指定 Workspace。未提供时，从当前目录向上查找最近 Git 根目录；
  非 Git 目录使用当前目录。
- `--session <name>`：Workspace 内 Session 名称，默认 `default`。

同名 Session 在不同 Workspace 中互相隔离。

## daemon 管理

```shell
learn-agent start
learn-agent status
learn-agent stop
```

`start` 不会自动启动 PostgreSQL。普通 Agent 状态使用本地 SQLite；只有显式启用 PostgreSQL Event Sink
或执行旧数据迁移时才需要 PostgreSQL。

## Agent 对话

交互式：

```shell
learn-agent chat --session default
```

单次：

```shell
learn-agent chat --session default "分析当前项目结构"
```

指定 Workspace：

```shell
learn-agent chat --workspace D:\project --session review "检查未提交修改"
```

复杂目标：

```shell
learn-agent chat --goal --session default "重构这部分代码并补充测试"
```

`--goal` 会启用父 Agent 的私有任务规划工具。该模式适合多步骤、跨文件、需要验证或可能因执行预算暂停后继续恢复的目标。普通 `learn-agent chat` 不暴露任务工具，避免短问题被不必要地拆解。

空输入不会发起请求。输入 `exit` 或 `quit` 退出交互模式。

## Session 控制

```shell
learn-agent session status --session default
learn-agent session resume --session default
learn-agent session resume --session default --instruction "继续，但只修改测试"
learn-agent session discard --session default
learn-agent session reset --session default
learn-agent session delete --session old-session
learn-agent session delete --session old-session --hard
```

- `status` 返回 pending Execution、checkpoint 状态和维护任务计数。
- `resume` 只适用于可恢复 Execution。
- `discard` 释放 Session，但不会删除已经提交的历史消息。
- `reset` 从 `messages` 表的归档消息重建 `sessions.recent_messages` 缓存，同时将 `context_tokens`
  重置为 0。用于解决 `recent_messages` 中包含导致 LLM 供应商拒绝请求的内容时的 session "卡死"问题。
- `delete` 默认归档 Session，使其不可继续使用但保留历史；`--hard` 才会永久删除 Session 及其本地关联数据。

## 工具审批

交互式 `learn-agent chat` 收到 `tool_approval_required` 后会原地显示工具名、审批原因和 capability，并提供：

```text
1=allow once, 2=deny once,
3=allow session, 4=deny session,
5=allow workspace, 6=deny workspace
```

后四项只在请求允许持久化时出现。按 Enter、`Ctrl+C` 或输入结束会保留待审批状态，不会默认批准或拒绝。提交决定后 CLI 调用 `approval.resolve` 并继续同一个 Execution；若恢复后再次遇到审批，会继续询问。

一次性 chat、自动化脚本、退出终端或 daemon 重启后，使用显式命令：

```shell
learn-agent approval list --session default
learn-agent approval resolve <request_id> allow_once --session default
learn-agent approval resolve <request_id> deny_once --session default
learn-agent approval resolve <request_id> allow_session --session default
learn-agent approval resolve <request_id> deny_workspace --session default
```

可用响应为 `allow_once`、`deny_once`、`allow_session`、`deny_session`、`allow_workspace` 和 `deny_workspace`。`list` 返回脱敏参数摘要、capability、原因和 `persistable`；`resolve` 会恢复原工具中断，因此不能用 `session resume` 替代。

Session 规则只影响当前 Session，Workspace 规则影响同一 Workspace 的后续 Session。复合 shell 命令只能单次审批，不能保存为持久规则。批准也不能绕过 Workspace 路径、符号链接、沙箱、主机执行和网络硬限制。 文件写入审批只显示操作、路径和大小，不显示正文；覆盖、移动、删除和 change set 应用只能单次批准。完整语义见 [Tool 安全、审批与 Hook 架构](/docs/architecture/tool-security-and-approval.md)。

## Hook 配置

```shell
learn-agent hooks path
learn-agent hooks init
learn-agent hooks init --path D:\hooks\hooks.json --force
learn-agent hooks init --project --workspace D:\project
learn-agent hooks validate --workspace D:\project
```

- `path` 显示 Core 会读取的 `hooks.json` 路径及文件是否存在。
- `init` 生成安全模板；模板默认不启用任何外部命令，只在 `_examples` 中给出配置示例。使用 `--project` 时写入 Workspace 下的 `.learn-agent/hooks.json`。
- `validate` 按当前配置解析 Hook 文件，但不会执行 Hook 命令；传入 `--workspace` 时会同时检查项目级路径。

默认用户级配置文件位于平台配置目录下的 `hooks.json`，例如 Windows 上通常是
`C:\Users\<user>\AppData\Local\learn-agent\hooks.json`。系统不会自动创建该文件。
## Trace 查询

```shell
learn-agent trace
learn-agent trace --run <run_id>
learn-agent trace --execution <execution_id>
learn-agent trace --layer llm
learn-agent trace --direction CORE_TO_PROVIDER
learn-agent trace --kind llm.response_finished
learn-agent trace --follow
learn-agent trace --raw --limit 50
```

Trace 命令直接读取本地 JSONL 文件，不要求 daemon 运行。多个过滤条件采用 AND 关系。

## TUI 终端界面

```shell
learn-agent tui
```

TUI（Terminal User Interface，终端用户界面）是一个基于 Textual 框架的富文本交互界面，
相比纯命令行模式提供更好的视觉反馈和操作体验。

### 界面布局

TUI 启动后分为三个区域：

- **顶部状态栏**：显示 Core daemon 连接状态（绿色=已连接，红色=断开），
  当前 Session（会话）名称，以及 goal 模式 / paused（暂停）标记。
- **中间事件日志**：显示 Agent 的流式输出。不同事件类型用不同颜色标记 —
  token（LLM 生成的文本片段）直接流式展示，错误用红色，暂停用黄色。工具调用明细默认折叠，可用 `Ctrl+O` 展开；goal 模式下的任务清单以一个最新进度块原地更新。
- **底部输入区**：支持多行输入，`Ctrl+Enter` 提交，`Ctrl+D` 退出。

### 快捷键与命令

| 操作 | 方式 | 说明 |
|---|---|---|
| 发送消息 | 输入文本后 `Ctrl+Enter` | 发送到当前 Session |
| 工具明细 | `Ctrl+O` | 展开或收起工具调用过程，任务进度块不受影响 |
| 工具明细 | `Ctrl+O` | 展开或收起工具调用过程；任务进度块始终显示最新状态 |
| 目标模式 | `/goal <消息>` | 发送一个复杂目标，Agent 会自主拆解为多步骤计划 |
| 恢复执行 | `/resume` | 恢复之前因预算限制暂停的 Execution（执行任务） |
| 丢弃任务 | `/discard` | 丢弃暂停的 Execution |
| 切换会话 | `/session <名称>` | 切换到指定 Session |
| 清屏 | `/clear` | 清除当前日志 |
| 帮助 | `/help` | 显示所有命令 |
| 退出 | `Ctrl+D` | 退出 TUI |

### 与 CLI 聊天模式的关系

TUI 和 `learn-agent chat` 使用相同的 JSON-RPC（基于 JSON 的远程过程调用）协议
和 daemon 认证机制，收到的事件流完全一致。二者的区别仅在于展示方式：
CLI 是纯文本输出，TUI 是结构化彩色界面。

TUI 断开连接后不会自动重试 `agent.chat`（避免重复执行），而是通过
`session.status` 检查是否有可恢复的 paused execution，让你决定是否恢复。

详细接入指南见 [TUI 与其他前端接入指南](/docs/api/tui-client-guide.md)。

## Core 管理命令

### `serve`

```shell
learn-agent-core serve [--host 127.0.0.1] [--port 18765]
```

在当前终端运行 Core，不创建后台进程。`--host` 仍必须是 loopback 地址。正常用户应使用
`learn-agent start`；`serve` 主要用于调试启动错误。

### `init-user-config`

```shell
learn-agent-core init-user-config --from-env .env [--force]
```

- `--from-env`：必须存在的源配置文件。
- `--force`：允许覆盖已有用户级配置。

该命令不会自动重启正在运行的 daemon。

### `migrate-workspace`

```shell
learn-agent-core migrate-workspace \
  --workspace <path> \
  [--keep-session default] \
  [--apply]
```

默认只执行 dry-run；`--apply` 才会修改旧 PostgreSQL Schema。检测到 daemon 正在运行时会拒绝执行。

### `migrate-local-state`

```shell
learn-agent-core migrate-local-state \
  --workspace <path> \
  [--keep-session default] \
  [--apply] \
  [--prune-source]
```

- 默认只执行 dry-run。
- `--apply` 执行导入。
- `--prune-source` 在验证导入后删除 PostgreSQL 中未保留的数据，必须与 `--apply` 一起使用。
- 检测到 daemon 正在运行时会拒绝执行。

详细流程见[PostgreSQL 到本地状态迁移](/docs/operations/local-state-migration.md)。

### `rollback-local-state`

```shell
learn-agent-core rollback-local-state \
  --from-version 11 \
  --to-version 10 \
  [--apply]
```

默认先验证当前版本和转换是否受支持，再输出 dry-run。`--apply` 会先获取 `state.db.operation.lock` 跨进程排他锁，并使用原子排他创建在 `state.db` 同目录生成带微秒时间戳和随机后缀的完整备份；备份复制与 SQLite `quick_check` 各自受 30 秒截止约束，随后才在单个事务中删除 v11 的资源活动派生表及迁移标记。备份失败会清理不完整文件且不会执行降级。当前只支持 `v11 -> v10`；daemon 运行或其他本地状态维护命令持锁时拒绝执行。
### `gc-artifacts`

```shell
learn-agent-core gc-artifacts
```

删除当前本地状态中没有引用关系的 Artifact。代码当前没有强制检查 daemon 是否运行；为避免与正在提交
的 Turn 竞争，执行前应先停止 daemon 并完成状态备份。

## 退出码和错误

CLI 将配置、连接、协议、鉴权和 Core 请求错误转换为用户可读信息。预期错误只显示用户可读信息；
未预期异常会额外显示异常类型和消息摘要作为 Hint，但不显示完整 Python traceback。

客户端开发应依赖 JSON-RPC 错误和流式 `error` 事件，而不是解析 CLI 输出。完整错误定义见
[错误参考](/docs/api/error-reference.md)。

## 当前限制

- 没有 Session 列表、历史查看或记忆管理命令。
- 没有正在执行任务的取消命令。
- 没有 daemon 自动重启、日志查看和备份命令。
- CLI 输出格式当前不是稳定机器接口；程序接入应使用 JSON-RPC。
