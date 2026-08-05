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
| `LEARN_AGENT_LLM_API_KEY` | 空 | Anthropic API 密钥。为空时进入无状态诊断模式，不执行真实 Agent Turn。 |
| `LEARN_AGENT_LLM_BASE_URL` | 空 | Anthropic API 地址。留空时使用 `ChatAnthropic` 默认地址。 |
| `LEARN_AGENT_MODEL` | `required (no default)` | 传给模型服务的模型名称，必须与服务端支持的名称一致。 |
| `LEARN_AGENT_MODEL_CONTEXT_LIMIT` | `192000` | Core 用于输入预算规划和 TUI 百分比显示的模型上下文窗口（token）。必须与服务商和模型的真实上限一致；提高该值不能扩展服务商能力。 |
| `LEARN_AGENT_LLM_MAX_TOKENS` | `49152` | 单次模型响应的最大输出 token。thinking/reasoning 与最终文本共享该预算；若耗尽，Turn 会以 `model_output_limit` 失败并保留诊断状态，不会误记为完成。 |
| `LEARN_AGENT_SUMMARY_TRIGGER_TOKEN_LIMIT_ENABLED` | `false` | 是否启用固定 Token 压缩门槛。关闭时不使用 `90000` 上限，只按输入硬上限和软比例动态计算；开启后固定门槛作为动态阈值的额外上限。 |
| `LEARN_AGENT_SUMMARY_TRIGGER_TOKEN_LIMIT` | `90000` | 可选的固定 Token 压缩门槛，仅在对应开关启用时生效；实际阈值为 `min(该值, 输入硬上限 × LEARN_AGENT_CONTEXT_SOFT_LIMIT_RATIO)`。 |
| `LEARN_AGENT_RECENT_TURN_LIMIT` | `1` | 压缩成功后默认只原样保留最新 1 个完整 Turn，允许设为 `0..3`；若最新 Turn 超出动态 Token 预算则可降为 0，且不会拆开工具调用周期。 |
| `LEARN_AGENT_RECENT_TURN_BUDGET_RATIO` | `0.5` | 原样 Turn 尾部最多占模型窗口的比例，取值须大于 `0` 且不超过 `0.5`。默认最多保留 1 个，超限时降为 0；显式提高 Turn 上限后会从配置值逐步减少。 |
| `LEARN_AGENT_CONTEXT_SAFETY_MARGIN_TOKENS` | `8192` | 为 provider 包装、估算误差和协议开销预留的输入安全空间。它与最大输出之和必须小于模型窗口。 |
| `LEARN_AGENT_CONTEXT_SOFT_LIMIT_RATIO` | `0.85` | 动态输入硬上限的软压缩比例。低于硬上限时压缩失败保留原文；达到硬上限时暂停等待可恢复压缩。 |
| `LEARN_AGENT_CONTEXT_SUMMARY_MAX_TOKENS` | `16384` | Session 历史摘要和 Turn 内工作摘要的最终模型输出预算（token）。摘要来源消息和摘要输出均不再由 Core 按字符截断；模型耗尽该预算时整次压缩失败并保留原始 Turn。 |
| `LEARN_AGENT_CONTEXT_SUMMARY_MAP_MAX_TOKENS` | `4096` | 超大历史进入 Map/Reduce 后，每个中间摘要的最大输出 token。完整来源能放入单次请求时不会使用该预算。 |
| `LEARN_AGENT_CONTEXT_SUMMARY_MAP_WORKERS` | `4` | Map/Reduce 摘要的最大并行模型调用数。同一 Session 仍只有一个压缩流程。 |
| `LEARN_AGENT_LLM_RETRY_ENABLED` | `true` | 是否启用 Core 统一 LLM 重试。启用后由 `ResilientModelProvider` 负责重试，SDK 内置重试保持关闭，避免重复重试。 |
| `LEARN_AGENT_LLM_FOREGROUND_MAX_ATTEMPTS` | `3` | 前台 Agent、子 Agent 和文件总结模型调用的最大尝试次数。内容审查、认证、无效请求等确定性错误不会重试。 |
| `LEARN_AGENT_LLM_BACKGROUND_MAX_ATTEMPTS` | `2` | 后台摘要和长期记忆提取等维护任务的模型调用最大尝试次数。耗尽后交回维护队列按任务级策略重试。 |
| `LEARN_AGENT_LLM_RETRY_BASE_DELAY_SECONDS` | `1` | 无服务端等待提示时的指数退避起始秒数。 |
| `LEARN_AGENT_LLM_RETRY_MAX_DELAY_SECONDS` | `30` | 单次模型重试等待的最大秒数；服务端 `Retry-After` 也会被此值截断。 |
| `LEARN_AGENT_LLM_RETRY_JITTER_RATIO` | `0.1` | 本地退避等待的随机抖动比例，用于避免多个请求同时恢复后再次撞到限流。 |
| `LEARN_AGENT_REASONING_DISPLAY` | `collapsed` | TUI/CLI 对模型 thinking/reasoning 块的展示模式：`hidden` 不发送，`metadata` 只显示字数，`collapsed` 发送可折叠内容，`expanded` 默认展开。 |
| `LEARN_AGENT_REASONING_PREVIEW_LIMIT` | `12000` | 单次 reasoning 内容可发送给前端的最大字符数；`metadata` 与 `hidden` 模式不会发送原文。 |

旧变量 `ALIYUN_API_KEY` 与 `ALIYUN_BASE_URL` 已废弃，不再作为默认 Anthropic 配置回退；新配置必须使用通用名称。

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
| `LEARN_AGENT_TOOL_APPROVAL_MODE` | `manual` | 全局工具审批模式。`manual` 在策略返回 `ASK` 时暂停并等待人工决定；`accept_all` 自动记录一次性允许，但不能绕过拒绝规则、Hook 拒绝和能力硬边界。 |
| `LEARN_AGENT_TOOL_APPROVAL_ENABLED` | 未设置 | 已废弃的兼容变量。仅当新变量未设置时生效：`true` 映射为 `manual`，`false` 映射为 `accept_all`。 |
| `LEARN_AGENT_HOST_EXECUTION_ENABLED` | `false` | 是否允许注册主机完全访问工具；此类工具仍必须逐次审批。 |
| `LEARN_AGENT_TOOL_DEFAULT_TIMEOUT_SECONDS` | `60` | 未声明专用超时的工具默认执行上限。 |
| `LEARN_AGENT_NETWORK_POLICY` | `deny` | 工具网络默认策略，与文件和命令权限分别判断。 |
| `LEARN_AGENT_FILE_WRITE_ENABLED` | `true` | 是否向 Parent Agent 注册受控 Workspace 写入工具；写入仍需通过审批与路径硬边界。 |
| `LEARN_AGENT_FILE_WRITE_MAX_BYTES` | `1048576` | 单个文本文件写入或替换后的 UTF-8 最大字节数。 |
| `LEARN_AGENT_FILE_OPERATION_MAX_ENTRIES` | `100` | 移动或递归删除目录时允许涉及的最大条目数。 |
| `LEARN_AGENT_COMMAND_WRITE_ENABLED` | `false` | 是否注册 staged command 工具；命令只修改临时副本，批准 change set 后才回写。 |
| `LEARN_AGENT_COMMAND_CHANGESET_MAX_FILES` | `100` | 单个命令 change set 允许包含的最大文件数。 |
| `LEARN_AGENT_COMMAND_CHANGESET_MAX_BYTES` | `10485760` | 单个命令 change set 的新增/修改文件总字节上限。 |
| `LEARN_AGENT_HOOKS_ENABLED` | `true` | 是否启用系统级 Agent 生命周期 Hook。 |
| `LEARN_AGENT_HOOK_CONFIG_FILES` | 空 | 额外 Hook JSON 文件；多个路径使用操作系统路径分隔符。 |
| `LEARN_AGENT_PROJECT_HOOKS_ENABLED` | `false` | 是否信任并加载 Workspace 内 `.learn-agent/hooks.json`。 |
| `LEARN_AGENT_CONTROLLED_EXECUTION_LIMIT_ENABLED` | `false` | 是否启用受控执行次数安全阀。默认关闭；关闭时仍统计命令和 Workspace 变更次数，但不因该子预算暂停。 |
| `LEARN_AGENT_MAX_CONTROLLED_EXECUTIONS_PER_GRANT` | `12` | 受控执行安全阀开启时，一次 Grant 允许的命令和 Workspace 变更调用数。它是总工具上限之下的可选子预算。 |
| `LEARN_AGENT_MAX_DELEGATIONS_PER_GRANT` | `6` | 父 Agent 委派子 Agent 的额度。 |
| `LEARN_AGENT_HARD_MAX_TOOL_CALLS_PER_GRANT` | `100` | 所有工具调用的紧急硬上限，用于阻止失控循环。 |

预算耗尽不会删除已提交历史；未完成执行可以通过 `learn-agent session resume` 继续。默认关闭受控执行子预算，是因为一次模型响应可以包含多个工具调用：固定的较低子上限会在正常长任务中提前暂停；总工具数、图步骤、Grant 时长、审批、沙箱和路径边界仍始终生效。

Hook 配置文件不会自动创建；使用 `learn-agent hooks path` 查看搜索路径，使用 `learn-agent hooks init` 生成安全模板。

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
- 上下文：近期 Turn 数量；摘要输入、分块和输出均使用 Token 预算。
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
control the parent Agent's private task planning tools. These tools are always
exposed to the parent Agent so ordinary and Goal requests share a stable tool
schema. Goal mode changes only the current user-turn prompt; tool execution
continues to obey the same Hook, approval, budget, and sandbox policies.

## Anthropic Prompt Cache

| 环境变量 | 默认值 | 含义与影响 |
|---|---:|---|
| `LEARN_AGENT_PROMPT_CACHE_ENABLED` | `true` | 全局前缀缓存开关。关闭后不向 tool、system prompt 或历史 message 注入 `cache_control`。 |
| `LEARN_AGENT_PROMPT_CACHE_TTL` | `5m` | Anthropic `cache_control.ttl`；仅支持 `5m`、`1h` 或空值。`1h` 为额外计费的扩展缓存，空值使用服务商默认时长。 |
| `LEARN_AGENT_PROMPT_CACHE_TOOLS` | `true` | 是否在最后一个稳定 tool schema 上添加 `cache_control`。 |
| `LEARN_AGENT_PROMPT_CACHE_SYSTEM` | `true` | 是否在 system prompt 的最后一个 text block 上添加 `cache_control`。 |
| `LEARN_AGENT_PROMPT_CACHE_MESSAGES` | `true` | 是否在本次模型调用前最深的稳定历史消息上添加 `cache_control`；工具循环中可落在最新 `tool_result`，初次请求不缓存末尾用户消息。 |

## Resource Activity

- `LEARN_AGENT_RESOURCE_ACTIVITY_ENABLED`：启用持久资源活动账本和通用前端 API，默认 `true`。
- `LEARN_AGENT_RESOURCE_ACTIVITY_HASH_ENABLED`：为可精确观测的文件计算版本 SHA-256，默认 `true`。
- `LEARN_AGENT_RESOURCE_ACTIVITY_MAX_ITEMS_PER_EXECUTION`：单 Execution 最多保存的明细数，默认 `1000`；超限后摘要返回 `truncated=true`。
