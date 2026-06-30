# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

本文档为中文编写，内容与 README.md、CONTRIBUTING.md 及 docs/ 目录下的设计文档保持一致。

## 项目概览

基于 LangChain、LangGraph 与本地 SQLite 状态库构建的本地 coding agent。采用 **双进程** 架构：

- **`learn-agent` (CLI)**：前台进程，负责命令解析、Workspace 发现、daemon 生命周期、用户 I/O 与流式展示。
- **`learn-agent-core` (Core daemon)**：后台进程，运行 LangGraph agent 循环、工具调用、LLM 调用、Session/记忆/执行状态与后台维护任务。
- **通信方式**：本地 TCP + NDJSON + JSON-RPC，使用临时 token 鉴权。

### 权威状态存储

| 存储 | 用途 | 是否权威 |
|---|---|---|
| `state.db` (SQLite) | Workspace、Session、消息、记忆、执行、维护任务 | ✅ 是 |
| `checkpoints.db` (SQLite) | LangGraph 可恢复 slice 的 checkpoint 线程 | 否 |
| `artifacts/` | 大型工具输出去重存储 | 否 |
| `telemetry/` | 领域观测事件（工具、记忆、上下文） | 否 |
| `traces/` | 跨层诊断时间线（JSONL） | 否 |
| PostgreSQL | 可选迁移源与事件 Sink | 否 |

### Agent 执行模型

```
Run:        一次 chat 或 resume 请求（一个 run_id）
Execution:  跨多个 Run 的任务（一个 execution_id）
Slice:      一次预算受限的 LangGraph 步骤批
```

核心组件：

- **AgentTurnService** 编排「加载 → 执行 → 提交」一轮 turn。
- **LangGraph** 通过 `StateGraph(MessagesState)` 驱动 agent ↔ tools 循环。
- **TurnCoordinator** 负责预算切分（slicing）与终结。
- **TurnFinalizer + CompletedTurnCommitter** 在同一 SQLite 事务中原子提交消息、上下文、执行状态与维护任务。
- **MaintenanceScheduler** 在后台运行上下文摘要、记忆抽取、checkpoint 清理等任务。
- **WorkspaceRuntimeRegistry** 缓存按 Workspace 编译的图（普通模式 + goal 模式）。

### 关键源码布局

```
src/
  config/        CLI 与 Core 共用配置（.env、路径、设置）
  ipc/           JSON-RPC 模型与本地凭据
  cli/           CLI 入口、命令、daemon 生命周期、渲染器
    commands/    每个子命令一个模块（chat、start、stop、status、session、trace）
  tui/           TUI 终端界面（基于 Textual 框架）
  core/
    app.py       组合根（composition root），串联所有服务
    main.py      Core daemon 入口（serve、migrate、init-user-config）
    agent/       AgentTurnService、LangGraph 图工厂、预算、Coordinator
    state/       SQLite schema、迁移、Repository（execution、workspace、checkpoint）
    workspace/   Workspace 身份识别、runtime 工厂与缓存
    tools/       工具注册表、实现、ObservedToolNode 包装器
    llm/         ModelProvider（默认 Anthropic，兼容 OpenAI 协议的旧路径）、模型配置
    context/     短期上下文管理
    memory/      消息归档与长期记忆抽取
    database/    可选 PostgreSQL 连接与旧数据迁移
    telemetry/   结构化观测事件、EventBus、sinks
    tracing/     跨层诊断时间线（JSONL）
    bus/         JSON-RPC 验证、鉴权、路由
    transport/   TCP + NDJSON 套接字服务
    handlers/    JSON-RPC 方法处理器（AgentHandlers、CoreHandlers）
    tasks/       Goal 模式私有任务规划（task repository、service）
    finalization/ 已完成 turn 提交与维护任务入队
    maintenance/ 任务队列、调度器、恢复协调
    errors/      Provider 错误分类与处理策略
    prompts/     父/子 Agent 系统 Prompt 模板
    hooks/       旧事件导入兼容层
    skills/      Skill 清单解析与存储
tests/
  unit/          单组件、纯逻辑、基于 mock 的测试
  integration/   基于本地 SQLite、TCP、线程池的多组件测试
  contracts/     文档与架构漂移检测
  optional/      需显式启用的测试（如 PostgreSQL）
```

## 常用命令

### Python 环境

项目使用位于 `D:\app\anaconda\envs\agent_learn` 的 Conda 环境。VSCode 已默认配置该环境（`.vscode/settings.json`）。

### 安装

```shell
D:/app/anaconda/envs/agent_learn/python.exe -m pip install -e .
```

### 测试

```shell
# 完整套件
python -B -m unittest discover -s tests -v

# 按类别
python -B -m unittest discover -s tests/unit -t . -v
python -B -m unittest discover -s tests/integration -t . -v
python -B -m unittest discover -s tests/contracts -t . -v

# 单个测试模块或方法
python -B -m unittest tests.unit.test_tracing -v
python -B -m unittest tests.integration.test_core_bus.CoreServerIntegrationTest -v

# 真实 PostgreSQL 集成测试（需先启动容器）
docker compose up -d postgres
$env:LEARN_AGENT_RUN_POSTGRES_INTEGRATION_TESTS="1"
python -B -m unittest tests.optional.test_memory_store -v
```

默认测试不要求启动 PostgreSQL，也不要求公网模型 API。

### 启动与使用 Agent

```shell
learn-agent start                                # 启动 Core daemon
learn-agent status                               # 检查 daemon 健康
learn-agent chat                                 # 交互式对话
learn-agent chat --session default "..."         # 单次提问
learn-agent chat --goal --session default "..."  # goal 模式（多步任务规划）
learn-agent tui                                  # 启动 TUI 终端界面
learn-agent stop                                 # 优雅停止
learn-agent stop --force                         # 强制终止卡死的 daemon
learn-agent session list                         # 列出 sessions
learn-agent session history --session default    # 查看 session 历史
learn-agent trace                                # 查看最近的 trace
```

TUI（Terminal User Interface）提供彩色事件流、流式 token 展示、自动暂停检测、`/` 快捷命令（`/goal`、`/resume`、`/discard`）和实时状态栏，适合需要持续交互的复杂任务。

### 首次配置

```shell
python -c "from shutil import copyfile; copyfile('.env.example', '.env')"
# 编辑 .env 填入 API 密钥，然后：
learn-agent-core init-user-config --from-env .env
learn-agent start
learn-agent status
learn-agent chat
```

CLI 与 Core daemon 共享同一个用户级配置目录（`LEARN_AGENT_ENV_FILE` 控制），不依赖启动命令所在目录。

## 配置

部署相关值通过 `.env` 或系统环境变量配置（参见 `.env.example`），不需修改源码。

| 变量 | 默认值 | 用途 |
|---|---|---|
| `LEARN_AGENT_LLM_API_KEY` | 空 | Anthropic API 密钥 |
| `LEARN_AGENT_LLM_BASE_URL` | 空 | Anthropic API 地址；留空时使用 `ChatAnthropic` 默认地址 |
| `LEARN_AGENT_MODEL` | required | 模型名称（必填，无默认） |
| `LEARN_AGENT_CORE_HOST` | `127.0.0.1` | Core 本地监听地址（仅 loopback） |
| `LEARN_AGENT_CORE_PORT` | `18765` | CLI 与 Core 使用的本地 TCP 端口 |
| `LEARN_AGENT_DB_HOST` | `127.0.0.1` | PostgreSQL 地址 |
| `LEARN_AGENT_DB_PORT` | `5432` | PostgreSQL 端口 |
| `LEARN_AGENT_DB_NAME` | `learn_agent` | 数据库名 |
| `LEARN_AGENT_DB_USER` | `postgres` | 数据库用户 |
| `LEARN_AGENT_DB_PASSWORD` | `postgres` | 数据库密码 |
| `LEARN_AGENT_DATABASE_URL` | 空 | 可替代全部分项数据库配置 |
| `LEARN_AGENT_STATE_DIR` | 用户数据目录下 `state/` | SQLite、Artifact 与 Telemetry 的父目录 |
| `LEARN_AGENT_RUNTIME_DIR` | 用户状态目录 | daemon 运行目录 |
| `LEARN_AGENT_REASONING_DISPLAY` | `collapsed` | 思考/推理内容展示策略：`metadata` / `collapsed` / `expanded` / `hidden` |

完整参数说明见 [配置参数参考](/docs/reference/configuration-reference.md)。
旧变量 `ALIYUN_API_KEY` 与 `ALIYUN_BASE_URL` 已废弃，请使用 `LEARN_AGENT_LLM_*` 通用变量。

### CLI Daemon 生命周期

- `start_daemon()` 将 Core 作为分离进程拉起，等待 HTTP ping。
- `stop_daemon()` 发送 `core.shutdown` RPC，等待进程退出。
- `--force` 在 Unix 上使用 SIGTERM → SIGKILL 终止卡死进程。

## Workspace 隔离

CLI 自动将当前目录最近的 Git 根目录识别为 Workspace；非 Git 目录使用当前目录。可使用
`--workspace <path>` 显式指定。Session、记忆、工具与 Skill 严格绑定 Workspace。

## 关键设计决策

- **本地优先**：SQLite `state.db` 是业务状态的权威源；PostgreSQL 仅用于 telemetry 与旧数据迁移。
- **至少一次提交（At-least-once commit）**：消息、上下文、执行状态与维护任务在同一 SQLite 事务中提交后才返回用户响应。
- **可恢复执行**：长任务切分为预算受限的 slice；暂停的执行可通过 `session resume` 恢复。
- **Goal 模式**：`--goal` 时父 agent 获得私有任务规划工具（`task_plan`、`task_update`、`task_list`、`task_get`），可在多次 chat/resume 周期内拆解复杂目标。
- **CLI 不触及状态**：CLI 只渲染事件，所有业务逻辑在 Core 中运行。
- **工具调用参数脱敏**：敏感参数（api_key、password、token 等）在 CLI 渲染时脱敏为 `[REDACTED]`，深度上限 20 层。

## 贡献与开发规范

参考 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [docs/development/](docs/development/) 下的开发指南。重要约束：

- 不提交 `.env`、daemon token、运行数据库、Trace 或其他本地运行数据。
- 优先复用现有边界，不为单次修改创建无职责抽象。
- 所有外部输入必须验证，所有 IO 必须有超时或大小边界。
- 新增功能必须包含测试与文档更新。
- 默认测试不得依赖公网模型 API 或必须运行的 PostgreSQL。
- 不兼容协议或 Schema 变更必须提供升级与回滚说明。
- Commit 描述一个清晰目的；PR 描述必须说明问题、方案、取舍、影响与验证结果。

## 相关文档

- [README.md](README.md) — 文档中心入口
- [CONTRIBUTING.md](CONTRIBUTING.md) — 贡献指南
- [docs/architecture/](docs/architecture/) — 架构与一致性机制
- [docs/api/](docs/api/) — RPC 与流式事件参考
- [docs/operations/runbook.md](docs/operations/runbook.md) — 日常运维 Runbook
- [docs/quality/testing-guide.md](docs/quality/testing-guide.md) — 测试结构与运行指南
