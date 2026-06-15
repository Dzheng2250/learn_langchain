# CLI 命令参考

> 文档状态：Current
> 权威范围：`learn-agent` 用户可用命令和参数
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

## 退出码和错误

CLI 将配置、连接、协议、鉴权和 Core 请求错误转换为用户可读信息，不默认展示 Python traceback。

客户端开发应依赖 JSON-RPC 错误和流式 `error` 事件，而不是解析 CLI 输出。完整错误定义见
[错误参考](/docs/api/error-reference.md)。

## 当前限制

- 没有 Session 列表、历史查看或记忆管理命令。
- 没有正在执行任务的取消命令。
- 没有 daemon 自动重启、日志查看和备份命令。
- CLI 输出格式当前不是稳定机器接口；程序接入应使用 JSON-RPC。
