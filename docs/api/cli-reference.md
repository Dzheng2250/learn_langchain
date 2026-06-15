# CLI 命令参考

> 文档状态：Current
> 权威范围：`learn-agent` 用户命令与 `learn-agent-core` 管理命令
> 维护触发：新增或修改 CLI 命令、参数、输出行为

## 命令总览

| 命令 | 用途 | 是否要求 daemon 运行 |
|---|---|---:|
| `learn-agent start` | 启动用户级 Core daemon | 否 |
| `learn-agent stop` | 请求 Core 优雅关闭 | 是 |
| `learn-agent status` | 检查 daemon 状态 | 否 |
| `learn-agent chat` | 交互式或单次 Agent 对话 | 是 |
| `learn-agent session status` | 查询 Session 和待恢复任务 | 是 |
| `learn-agent session resume` | 恢复待执行任务 | 是 |
| `learn-agent session discard` | 丢弃待恢复任务 | 是 |
| `learn-agent trace` | 查询本地系统 Trace | 否 |

`learn-agent-core` 是管理与调试入口，不用于日常对话：

| 命令 | 用途 | daemon 必须停止 |
|---|---|---:|
| `learn-agent-core serve` | 前台运行 Core daemon，便于调试 | 是 |
| `learn-agent-core init-user-config` | 将指定 `.env` 复制到用户级配置目录 | 否；配置生效需要重启 |
| `learn-agent-core migrate-workspace` | 迁移旧 PostgreSQL Workspace Schema | 是 |
| `learn-agent-core migrate-local-state` | 将保留 Session 导入本地权威状态 | 是 |
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

空输入不会发起请求。输入 `exit` 或 `quit` 退出交互模式。

## Session 控制

```shell
learn-agent session status --session default
learn-agent session resume --session default
learn-agent session resume --session default --instruction "继续，但只修改测试"
learn-agent session discard --session default
```

- `status` 返回 pending Execution、checkpoint 状态和维护任务计数。
- `resume` 只适用于可恢复 Execution。
- `discard` 释放 Session，但不会删除已经提交的历史消息。

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
