# 日常运维 Runbook

> 文档状态：Current
> 权威范围：Core daemon、Session、后台维护和本地状态的日常检查与故障处理
> 维护触发：CLI 命令、运行目录、恢复流程或故障处理方式变化

Runbook 是可直接执行的运维操作手册。它回答“系统现在是否正常”和“异常后先做什么”，
不解释内部实现细节。架构原理见[系统架构总览](/docs/architecture/system-overview.md)。

## 1. 数据与进程边界

| 内容 | 作用 | 是否影响对话恢复 |
|---|---|---|
| Core daemon | 真正执行 Agent、工具和维护任务 | 是，停止后不能发起新请求 |
| `state.db` | Session、消息、记忆、Execution 和维护任务的权威状态 | 是，必须保护 |
| `checkpoints.db` | LangGraph 未完成执行的恢复点 | 仅影响未完成任务恢复 |
| `artifacts/` | 大型持久化内容 | 可能影响历史内容读取 |
| `traces/`、`telemetry/` | 排障和性能诊断 | 否，可按保留策略清理 |
| PostgreSQL | 可选 Telemetry、旧数据迁移或未来投影 | 普通对话不依赖 |

默认本地状态目录由平台决定，可通过 `LEARN_AGENT_STATE_DIR` 覆盖。具体路径见
[配置参数参考](/docs/reference/configuration-reference.md)。

## 2. 日常健康检查

```shell
learn-agent status
learn-agent session status --session default
learn-agent trace --limit 50
```

预期结果：

- `learn-agent status` 返回 daemon 正在运行及其 uptime。
- Session 没有异常的 `pending_execution`，维护任务没有持续增长的 `failed`。
- Trace 中请求能够到达 `ipc.response_sent`，Agent 请求能够到达完成或明确暂停事件。

## 3. 启动、停止和重启

启动：

```shell
learn-agent start
learn-agent status
```

正常停止：

```shell
learn-agent stop
learn-agent status
```

修改用户级 `.env` 后必须重启：

```shell
learn-agent stop
learn-agent-core init-user-config --from-env .env --force
learn-agent start
```

不要在 daemon 运行时手工替换 `state.db` 或 `checkpoints.db`。

## 4. Session 无法继续

先检查：

```shell
learn-agent session status --session default
```

常见状态：

- `paused_budget`：本次执行达到预算限制，可使用 `resume` 继续。
- `paused_error`：Provider、工具或图执行失败；确认原因后选择恢复或丢弃。
- `unrecoverable_checkpoint`：权威状态认为存在未完成任务，但 checkpoint 已缺失，不能自动恢复。

恢复未完成任务：

```shell
learn-agent session resume --session default --instruction "继续并先总结当前进度"
```

放弃未完成任务：

```shell
learn-agent session discard --session default
```

`discard` 只放弃待恢复 Execution，不删除已经提交的历史消息、长期记忆或整个 Session。

## 5. Provider 或敏感内容错误

Provider 拒绝请求时，Core 会将错误分类并暂停或释放当前 Execution。处理顺序：

1. 查看 CLI 展示的用户可读错误。
2. 使用 `session status` 确认是否还有 pending Execution。
3. 不需要恢复该输入时执行 `session discard`。
4. 重新发起不包含被拒绝内容的新请求。
5. 若仍失败，使用 Trace 按 `run_id` 或 `execution_id` 检查 Provider 调用。

不要通过直接修改 SQLite 删除 pending 状态。

## 6. 后台维护异常

后台维护负责摘要、长期记忆提取和 checkpoint 清理。普通对话提交成功后，这些任务可以稍后完成。

检查：

```shell
learn-agent session status --session default
```

若 `maintenance.failed` 持续大于零：

1. 检查 daemon 日志和 Trace。
2. 确认模型配置、文件权限和本地磁盘空间。
3. 正常重启 Core，使可重试任务重新被调度。
4. 不要删除 `maintenance_jobs` 表中的记录；当前没有公开的维护任务管理命令。

维护失败不会撤销已经提交的完整对话，但可能导致摘要、记忆或 checkpoint 清理滞后。

## 7. Trace 与日志排障

```shell
learn-agent trace --run <run_id>
learn-agent trace --execution <execution_id>
learn-agent trace --layer llm
learn-agent trace --kind llm.request_failed
learn-agent trace --follow
```

Trace 是 best-effort 诊断数据，不是业务事实。Trace 缺失不代表请求没有执行，最终状态应以
`state.db` 和 Session 状态为准。

## 8. 磁盘空间与清理

可以按保留策略清理：

- 过期 Trace 日期目录。
- 不再需要的 Telemetry JSONL。
- 已确认无引用的临时文件。

不得在不了解引用关系时手工删除：

- `state.db`
- `checkpoints.db`
- `artifacts/`
- SQLite 的 `-wal`、`-shm` 文件

删除或移动本地状态前，先停止 daemon 并按
[备份与恢复](/docs/operations/backup-and-restore.md)创建快照。

## 9. 当前运维限制

- 没有自动备份调度和一键恢复命令。
- 没有公开的 Session 列表、历史管理或维护任务管理命令。
- 没有操作系统服务注册，系统重启后需要显式启动 daemon。
- Trace 和 Telemetry 不能作为计费、合规审计或业务恢复依据。

这些限制统一登记在[路线图与已知限制](/docs/product/roadmap-and-known-limitations.md)。
