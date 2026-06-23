# 配置参数参考

> 文档状态：Current
> 权威范围：环境变量、默认值、类型、单位、作用域和调整风险
> 维护触发：新增、删除、重命名或修改配置项默认值与语义

## 本文负责

- 配置项名称、类型、默认值、单位、作用域和调整风险。

## 本文不负责

- 不提供完整部署流程；见 Deployment。
- 不解释配置设计取舍或业务实现。


本文是项目配置参数的权威参考。快速开始见 [`README.md`](/README.md)，部署与 Docker
说明见 [`/docs/operations/deployment.md`](/docs/operations/deployment.md)，配置代码的设计边界见
[`/docs/decisions/configuration-and-domain-constants.md`](/docs/decisions/configuration-and-domain-constants.md)。

## 1. 配置如何生效

配置优先级从高到低为：

```text
进程环境变量
  -> LEARN_AGENT_ENV_FILE 指定的文件
  -> 用户级配置目录中的 .env
  -> src/config/settings.py 中的默认值
```

CLI 和 Core 启动时读取配置。除单次命令参数外，修改配置后需要重启 Core：

```shell
learn-agent stop
learn-agent start
```

环境变量适合部署者调整；未提供环境变量的源码常量属于当前实现策略，修改它们需要改代码并运行测试。

## 2. 模型配置

| 环境变量 | 默认值 | 含义与影响 |
|---|---:|---|
| `LEARN_AGENT_LLM_API_KEY` | 空 | OpenAI 兼容模型 API 密钥。为空时进入无状态诊断模式，不执行真实 Agent Turn。 |
| `LEARN_AGENT_LLM_BASE_URL` | 空 | OpenAI 兼容 API 地址。留空时使用客户端默认地址。 |
| `LEARN_AGENT_MODEL` | `required (no default)` | 传给模型服务的模型名称，必须与服务端支持的名称一致。 |
| `LEARN_AGENT_MODEL_CONTEXT_LIMIT` | `128000` | 模型上下文窗口大小（token），用于 TUI 显示上下文使用百分比。不影响实际提交给模型的 token 数量。 |
| `LEARN_AGENT_SUMMARY_TRIGGER_TOKEN_LIMIT` | `5000` | 上下文 token 数超过此值时触发自动压缩。测试阶段默认 5K，生产环境建议设为模型上下文窗口的 80%。 |
| `LEARN_AGENT_LLM_STREAM_USAGE_ENABLED` | `true` | 流式调用时请求服务商返回 Token usage。若兼容接口拒绝 `stream_options.include_usage`，设为 `false`。 |
| `LEARN_AGENT_LLM_RETRY_ENABLED` | `true` | 是否启用 Core 统一 LLM 重试。启用后由 `ResilientModelProvider` 负责重试，SDK 内置重试保持关闭，避免重复重试。 |
| `LEARN_AGENT_LLM_FOREGROUND_MAX_ATTEMPTS` | `3` | 前台 Agent、子 Agent 和文件总结模型调用的最大尝试次数。内容审查、认证、无效请求等确定性错误不会重试。 |
| `LEARN_AGENT_LLM_BACKGROUND_MAX_ATTEMPTS` | `2` | 后台摘要和长期记忆提取等维护任务的模型调用最大尝试次数。耗尽后交回维护队列按任务级策略重试。 |
| `LEARN_AGENT_LLM_RETRY_BASE_DELAY_SECONDS` | `1` | 无服务端等待提示时的指数退避起始秒数。 |
| `LEARN_AGENT_LLM_RETRY_MAX_DELAY_SECONDS` | `30` | 单次模型重试等待的最大秒数；服务端 `Retry-After` 也会被此值截断。 |
| `LEARN_AGENT_LLM_RETRY_JITTER_RATIO` | `0.1` | 本地退避等待的随机抖动比例，用于避免多个请求同时恢复后再次撞到限流。 |

旧变量 `ALIYUN_API_KEY` 与 `ALIYUN_BASE_URL` 仅作为兼容回退，新配置应使用通用名称。

## 3. Core、CLI 与本地路径

| 环境变量 | 默认值 | 含义与影响 |
|---|---:|---|
| `LEARN_AGENT_CORE_HOST` | `127.0.0.1` | Core TCP 监听地址。仅允许 loopback 地址或 `localhost`，不得暴露到外部网络。 |
| `LEARN_AGENT_CORE_PORT` | `18765` | CLI 与 Core 通信端口。修改后 CLI 与 Core 必须使用同一配置。 |
| `LEARN_AGENT_CORE_CONNECT_TIMEOUT_SECONDS` | `3` | CLI 建立 Core 连接的超时秒数。 |
| `LEARN_AGENT_CORE_SHUTDOWN_TIMEOUT_SECONDS` | `10` | Core 关闭 Transport、连接池等资源时的等待秒数。 |
| `LEARN_AGENT_DAEMON_STARTUP_TIMEOUT_SECONDS` | `15` | `learn-agent start` 等待 daemon 可用的最长秒数。 |
| `LEARN_AGENT_DAEMON_STOP_TIMEOUT_SECONDS` | `15` | `learn-agent stop` 等待 daemon 退出的最长秒数。 |
| `LEARN_AGENT_CORE_AGENT_WORKERS` | `4` | 可同时执行的不同 Session Turn 数量；同一 Session 仍串行。调大可能增加模型、工具和 SQLite 写竞争。 |
| `LEARN_AGENT_ENV_FILE` | 平台用户配置目录下 `.env` | 覆盖用户级配置文件位置。 |
| `LEARN_AGENT_RUNTIME_DIR` | 平台用户状态目录下 `runtime/` | PID、token 和 daemon 日志目录。 |
| `LEARN_AGENT_STATE_DIR` | 平台用户数据目录下 `state/` | `state.db`、`checkpoints.db`、Artifact 与 Telemetry 的父目录。 |
| `LEARN_AGENT_DEBUG` | `false` | 启用调试输出。可能产生更多日志，不应在敏感生产环境长期开启。 |

`LEARN_AGENT_STATE_DIR` 中的主要内容：

```text
state.db        Session、消息、长期记忆、Execution 和维护任务
checkpoints.db  LangGraph 可恢复执行断点
artifacts/      大型持久化内容
telemetry/      默认 JSONL 观测事件
```

## 4. Agent 执行预算

一次用户请求获得一个 Grant；Grant 可以包含多个有界 Slice。详细概念见
[`/docs/architecture/resumable-execution.md`](/docs/architecture/resumable-execution.md)。

| 环境变量 | 默认值 | 含义与影响 |
|---|---:|---|
| `LEARN_AGENT_MAX_GRAPH_STEPS_PER_SLICE` | `20` | 单个 LangGraph Slice 的最大图步骤数。过小会频繁暂停，过大则单次执行更难控制。 |
| `LEARN_AGENT_MAX_AUTO_SLICES_PER_GRANT` | `3` | 一次 chat/resume 最多自动继续的 Slice 数。 |
| `LEARN_AGENT_MAX_GRANT_WALL_SECONDS` | `600` | 单个 Grant 的协作式总时长上限，单位为秒。 |
| `LEARN_AGENT_MAX_PARALLEL_TOOL_CALLS` | `4` | 同一 Grant 中允许并行执行的工具数。 |
| `LEARN_AGENT_MAX_CONTROLLED_EXECUTIONS_PER_GRANT` | `12` | 命令、容器等高风险受控执行的额度。 |
| `LEARN_AGENT_MAX_DELEGATIONS_PER_GRANT` | `6` | 父 Agent 委派子 Agent 的额度。 |
| `LEARN_AGENT_HARD_MAX_TOOL_CALLS_PER_GRANT` | `100` | 所有工具调用的紧急硬上限，用于阻止失控循环。 |

预算耗尽不会删除已提交历史；未完成执行可以通过 `learn-agent session resume` 继续。

## 5. 本地状态与 PostgreSQL

当前普通对话只依赖本地 SQLite。PostgreSQL 是可选迁移来源、可选 Telemetry Sink 和未来投影目标。

### 后端选择

这些变量是面向接口重构后的实现选择开关。当前版本只提供生产级 `sqlite` 适配器，
因此它们主要用于固定架构边界和后续扩展，不建议改成其他值。

| 环境变量 | 默认值 | 含义与影响 |
|---|---:|---|
| `LEARN_AGENT_CONVERSATION_HISTORY_BACKEND` | `sqlite` | 会话历史后端。未来可接入 JSONL 文件或 PostgreSQL。 |
| `LEARN_AGENT_MEMORY_BACKEND` | `sqlite` | 长期记忆后端。当前权威数据仍在本地 SQLite。 |
| `LEARN_AGENT_TASK_BACKEND` | `sqlite` | goal 模式私有任务计划后端。 |
| `LEARN_AGENT_CHECKPOINT_BACKEND` | `sqlite` | 可恢复执行 checkpoint 后端。 |

### PostgreSQL

| 环境变量 | 默认值 | 含义与影响 |
|---|---:|---|
| `LEARN_AGENT_POSTGRES_PROJECTION_ENABLED` | `false` | 是否为未来 PostgreSQL 投影写入本地 outbox。当前没有完整投影消费者，默认必须关闭。 |
| `LEARN_AGENT_DATABASE_URL` | 空 | PostgreSQL 完整连接 URL；设置后优先于分项配置。 |
| `LEARN_AGENT_DB_HOST` | `127.0.0.1` | PostgreSQL 地址。 |
| `LEARN_AGENT_DB_PORT` | `5432` | PostgreSQL 端口。 |
| `LEARN_AGENT_DB_NAME` | `learn_agent` | PostgreSQL 数据库名。 |
| `LEARN_AGENT_DB_USER` | `postgres` | PostgreSQL 用户。 |
| `LEARN_AGENT_DB_PASSWORD` | `postgres` | PostgreSQL 密码。仅适合本地开发，非本地环境必须修改。 |
| `LEARN_AGENT_DB_CONTAINER` | `learn-agent-postgres` | 迁移备份回退到 Docker `pg_dump` 时使用的容器名。 |
| `LEARN_AGENT_PG_DUMP_PATH` | 空 | 显式指定本机 `pg_dump` 路径。 |

只有启用 PostgreSQL Telemetry、执行旧数据迁移或使用相关集成测试时，才需要启动 PostgreSQL。

## 6. Telemetry 配置

Telemetry 用于审计和诊断，不决定业务 Turn 成败。详细设计见
[`/docs/architecture/event-system.md`](/docs/architecture/event-system.md)。

### System Trace

System Trace 默认启用，只保存经过脱敏和截断的跨层摘要。完整设计见
[`/docs/architecture/system-tracing.md`](/docs/architecture/system-tracing.md)。

| 环境变量 | 默认值 | 含义 |
|---|---:|---|
| `LEARN_AGENT_TRACE_ENABLED` | `true` | 是否记录本地系统 Trace |
| `LEARN_AGENT_TRACE_DIR` | 空 | 显式 Trace 根目录；为空时使用本地状态目录下的 `traces` |
| `LEARN_AGENT_TRACE_RETENTION_DAYS` | `14` | 按 UTC 日期轮转后的保留天数 |
| `LEARN_AGENT_TRACE_BATCH_SIZE` | `100` | 后台 Writer 单批最大记录数 |
| `LEARN_AGENT_TRACE_FLUSH_INTERVAL_SECONDS` | `0.5` | 未满批次时的最大等待秒数 |
| `LEARN_AGENT_TRACE_QUEUE_MAX_SIZE` | `5000` | 有界内存队列容量；满时丢弃 Trace，不阻塞业务 |
| `LEARN_AGENT_TRACE_DATA_PREVIEW_LIMIT` | `500` | 单个摘要字符串最大字符数 |

| 环境变量 | 默认值 | 含义与影响 |
|---|---:|---|
| `LEARN_AGENT_EVENTS_SQLITE_ENABLED` | `true` | 将结构化 Telemetry 异步写入独立的本地 SQLite 数据库。 |
| `LEARN_AGENT_EVENTS_SQLITE_PATH` | 空 | 显式指定 Telemetry SQLite 路径；为空时使用 `state/telemetry/events.db`。 |
| `LEARN_AGENT_EVENTS_SQLITE_RETENTION_DAYS` | `30` | Core 启动时删除超过该天数的本地 Telemetry。 |
| `LEARN_AGENT_EVENTS_FILE_ENABLED` | `true` | 将事件异步写入本地 JSONL。 |
| `LEARN_AGENT_EVENTS_FILE_PATH` | 空 | 显式指定事件 JSONL 路径；为空时使用本地状态目录下的默认路径。 |
| `LEARN_AGENT_EVENTS_POSTGRES_ENABLED` | `false` | 启用 PostgreSQL Event Sink；启用后 PostgreSQL 成为可选运行依赖。 |
| `LEARN_AGENT_EVENTS_ASYNC_WRITE` | `true` | 使用有界后台队列和批量写入；生产环境不应关闭。 |
| `LEARN_AGENT_EVENTS_BATCH_SIZE` | `50` | 单次 SQLite/JSONL/PostgreSQL 批量写入的最大事件数。 |
| `LEARN_AGENT_EVENTS_FLUSH_INTERVAL_SECONDS` | `1.0` | 批次未满时的最大等待时间。 |
| `LEARN_AGENT_EVENTS_QUEUE_MAX_SIZE` | `1000` | 每个缓冲 Sink 的队列容量；满时丢弃新事件而不阻塞 Agent。 |
| `LEARN_AGENT_EVENTS_PAYLOAD_PREVIEW_LIMIT` | `1000` | payload 中单个预览值的字符上限。 |

PostgreSQL Event Sink 当前要求目标数据库已经具有 `agent_events` 表；普通 Core 启动不会自动创建
PostgreSQL Schema。若只是需要本地结构化观测记录，应保持该选项关闭并使用默认 SQLite Sink。

队列越大，短时突发承载能力越高，但进程异常退出时可能丢失更多尚未落盘的观测事件。

## 7. 后台维护配置

后台维护处理摘要、长期记忆提取和 checkpoint 清理。任务可靠保存在
`state.db.maintenance_jobs`，不是仅存在于内存。

| 环境变量 | 默认值 | 含义与影响 |
|---|---:|---|
| `LEARN_AGENT_MAINTENANCE_POLL_INTERVAL_SECONDS` | `0.25` | Scheduler 查询待处理任务的间隔秒数。过小会增加空轮询，过大会增加后台任务延迟。 |
| `LEARN_AGENT_MAINTENANCE_LEASE_SECONDS` | `60` | Worker 认领任务的租约秒数。进程崩溃后，租约过期的任务可被重新认领。 |
| `LEARN_AGENT_MAINTENANCE_SHUTDOWN_TIMEOUT_SECONDS` | `5` | Core 关闭时等待维护 Worker 停止的秒数。 |
| `LEARN_AGENT_MAINTENANCE_MAX_ATTEMPTS` | `5` | 维护任务默认最大尝试次数。 |
| `LEARN_AGENT_MAINTENANCE_MAX_RETRY_DELAY_SECONDS` | `300` | 指数退避的最大等待秒数。 |
| `LEARN_AGENT_MAINTENANCE_ERROR_PREVIEW_LIMIT` | `2000` | 保存到任务记录中的错误摘要字符上限。 |

## 8. 当前仍属于源码策略的参数

以下参数会影响行为，但尚未完成类型化配置迁移，因此不能通过 `.env` 调整：

- 工具与 Docker：镜像、超时、内存、CPU、输出截断长度。
- 文件读取与大文件总结：分块行数、最大块数、并行 Map Worker 数。
- 子 Agent：最大步骤、继承的上下文消息数、结果长度。
- 上下文：近期消息数量、摘要长度。
- 长期记忆：bootstrap/relevant 数量、提取周期、重要度下限。
- Skill：目录名、文件名和内容读取上限。

它们集中定义在 `src/config/settings.py`。后续应按功能域迁移为
`ToolSettings`、`ContextSettings`、`MemorySettings`、`TelemetrySettings` 等类型化对象，
由 `CoreApp` 统一加载和注入。

## 9. 调整配置时的原则

1. 先确认参数属于“部署配置”还是“业务策略”，不要把所有常量都暴露成环境变量。
2. 修改执行预算、并发、队列和超时时，必须同时考虑资源消耗与用户等待时间。
3. 修改 SQLite、本地状态路径或 PostgreSQL 配置前，应先停止 Core 并备份数据。
4. 修改配置后运行完整测试，并通过 `learn-agent session status` 检查维护任务和待恢复执行。

## Goal-mode private task planning variables

`LEARN_AGENT_TASK_MAX_PER_EXECUTION`, `LEARN_AGENT_TASK_KEY_MAX_CHARS`,
`LEARN_AGENT_TASK_SUBJECT_MAX_CHARS`, `LEARN_AGENT_TASK_DESCRIPTION_MAX_CHARS`,
`LEARN_AGENT_TASK_NOTES_MAX_CHARS`, and `LEARN_AGENT_TASK_LIST_OUTPUT_LIMIT`
control the parent Agent's private task planning tools. These tools are exposed
only when a request uses goal mode, for example `learn-agent chat --goal ...`.
