# Learn LangChain Agent

架构与运行机制：

- [文档中心](/docs/README.md)
- [项目概述与能力边界](/docs/product/project-overview.md)
- [系统架构总览](/docs/architecture/system-overview.md)
- [前端与 TUI 接入指南](/docs/api/tui-client-guide.md)
- [RPC 方法参考](/docs/api/rpc-reference.md)
- [流式事件参考](/docs/api/streaming-events.md)
- [本地优先 Session 状态](/docs/architecture/local-first-session-state.md)
- [本地数据库设计与一致性机制](/docs/architecture/database-state-and-consistency.md)
- [可恢复执行与预算控制](/docs/architecture/resumable-execution.md)
- [私有任务规划](/docs/architecture/private-task-planning.md)
- [PostgreSQL 到本地状态迁移](/docs/operations/local-state-migration.md)
- [本地优先优化的原因、风险与设计审查](/docs/decisions/local-first-rationale-and-review.md)
- [最终响应、后台维护与 Checkpoint 一致性](/docs/architecture/response-finalization-and-checkpoint-consistency.md)
- [配置参数参考](/docs/reference/configuration-reference.md)
- [配置、领域常量与 Prompt 管理边界](/docs/decisions/configuration-and-domain-constants.md)
- [日常运维 Runbook](/docs/operations/runbook.md)
- [开发与贡献指南](/CONTRIBUTING.md)

这是一个基于 LangChain、LangGraph 和本地 SQLite 状态库的 coding agent。

项目采用双进程结构：

- `learn-agent`：前台 CLI，负责用户交互和流式展示。
- `learn-agent-core`：后台 Core daemon，负责 Agent、工具、上下文和记忆。
- SQLite：保存 Workspace、Session、完整消息、长期记忆和可恢复执行状态，是本地业务状态的权威来源。
- PostgreSQL：作为可选的迁移来源、Telemetry Sink 和未来查询投影，不参与普通对话的必要提交。

## 快速开始

前置条件：

- Python 3.11 或更高版本。
- Docker 与 Docker Compose。仅使用容器命令沙箱、PostgreSQL 可选功能或数据库迁移时需要。
- OpenAI 兼容模型 API。仅验证基础设施时可以暂不配置。

### 1. 安装项目

```shell
python -m pip install -e .
```

### 2. 创建本地配置

以下命令在 Windows、Linux 和 macOS 均可运行：

```shell
python -c "from shutil import copyfile; copyfile('.env.example', '.env')"
```

需要真实 Agent 回答时设置：

```dotenv
LEARN_AGENT_LLM_API_KEY=your-api-key
LEARN_AGENT_LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
```

未配置 API 密钥时，仍可启动 Core 并发起会话。请求会完整经过 CLI、JSON-RPC、Workspace、
Session 和数据库创建/读取链路，随后返回统一诊断提示，但不会调用 LLM 或工具，也不会写入
对话历史或消耗业务轮次。

### 3. 可选：启动 PostgreSQL

```shell
docker compose up -d postgres
docker compose ps
```

Compose 使用 `pgvector/pgvector:pg17`，数据保存在 Docker named volume
`learn_agent_postgres_data` 中，并且只将数据库端口绑定到 `127.0.0.1`。该名称是 Compose
文件中的逻辑名，实际 Docker 卷名通常带有 Compose 项目前缀。

普通对话的权威状态保存在本地 `state.db`，不要求 PostgreSQL 运行。只有启用 PostgreSQL
Telemetry、执行旧数据迁移或运行相关数据库集成测试时才需要启动它。开发环境默认数据库账号和
密码均为 `postgres`，非本地环境必须修改密码。

### 4. 初始化用户级配置

```shell
learn-agent-core init-user-config --from-env .env
```

CLI 和 Core daemon 从同一个用户级配置目录读取配置，不依赖启动命令所在目录。

### 5. 启动并使用 Agent

```shell
learn-agent start
learn-agent status
learn-agent chat
```

单次提问：

```shell
learn-agent chat --session default "查看当前项目结构"
```

复杂目标可显式启用 goal 模式。该模式会让父 Agent 获得私有任务规划工具，用于拆解多步骤目标、记录依赖和跨 resume 延续计划：

```shell
learn-agent chat --goal --session default "重构这部分代码并补充测试"
```

普通 `learn-agent chat` 不暴露任务规划工具，适合短问题和单步操作。

TUI 终端界面（Terminal User Interface，基于 Textual 框架的富文本终端界面）：

```shell
learn-agent tui
```

TUI 提供彩色事件流、流式 token 展示、自动暂停检测、`/` 快捷命令（`/goal`、`/resume`、`/discard`）和实时状态栏，适合需要持续交互的复杂任务。

停止服务：

```shell
learn-agent stop
```

Core 首次启动时会自动创建本地 SQLite Schema。若启用了 PostgreSQL 可选能力，数据库未启动、
凭据错误或检测到需要显式迁移的旧 PostgreSQL Schema 时，对应能力会失败。

## 配置方式

部署相关值通过 `.env` 或操作系统环境变量配置，不需要修改源码。

常用变量：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `LEARN_AGENT_LLM_API_KEY` | 空 | OpenAI 兼容模型 API 密钥 |
| `LEARN_AGENT_LLM_BASE_URL` | 空 | OpenAI 兼容 API 地址；留空时使用客户端默认地址 |
| `LEARN_AGENT_MODEL` | `required (no default)` | 模型名称 |
| `LEARN_AGENT_DB_HOST` | `127.0.0.1` | PostgreSQL 地址 |
| `LEARN_AGENT_DB_PORT` | `5432` | PostgreSQL 端口 |
| `LEARN_AGENT_DB_NAME` | `learn_agent` | 数据库名 |
| `LEARN_AGENT_DB_USER` | `postgres` | 数据库用户 |
| `LEARN_AGENT_DB_PASSWORD` | `postgres` | 数据库密码 |
| `LEARN_AGENT_DATABASE_URL` | 空 | 可替代全部分项数据库配置 |
| `LEARN_AGENT_ENV_FILE` | 用户配置目录下 `.env` | 显式指定 Core 配置文件 |
| `LEARN_AGENT_RUNTIME_DIR` | 用户状态目录 | 显式指定 daemon 运行目录 |
| `LEARN_AGENT_STATE_DIR` | 用户数据目录下 `state/` | 显式指定 SQLite、Artifact 和 Telemetry 的父目录 |
| `LEARN_AGENT_CORE_HOST` | `127.0.0.1` | Core 本地监听地址；仅允许 loopback |
| `LEARN_AGENT_CORE_PORT` | `18765` | CLI 与 Core 使用的本地 TCP 端口 |

全部参数的默认值、单位、调整影响和风险见[配置参数参考](/docs/reference/configuration-reference.md)。
完整部署方式、已有 Docker 数据目录复用、故障排查和安全边界见[部署指南](/docs/operations/deployment.md)。

旧变量 `ALIYUN_API_KEY` 和 `ALIYUN_BASE_URL` 暂时保留兼容，但已弃用；通用变量优先。

## Workspace 隔离

CLI 会将当前目录最近的 Git 根目录识别为 Workspace；非 Git 目录使用当前目录。同一个用户级
daemon 可以从任意目录访问，但 Session、记忆、工具和 Skill 严格绑定当前 Workspace。

可使用 `--workspace <path>` 显式指定 Workspace。

## 项目结构

```text
compose.yaml         PostgreSQL + pgvector 本地部署
.env.example         配置模板

src/
  config/            CLI 与 Core 共用配置
  ipc/               JSON-RPC 模型和本地凭据
  cli/               CLI 命令、client 和 daemon 管理
  tui/               TUI 终端界面（Textual 框架）
    client.py        异步 JSON-RPC 客户端
    renderer.py      Rich 标记事件渲染器
    widgets/         状态栏、事件日志、输入框
    screens/         聊天屏幕
  core/
    app.py           依赖组装与生命周期管理
    bus/             JSON-RPC 验证、鉴权和路由
    transport/       TCP 与 NDJSON 传输
    agent/           LangGraph 和 AgentTurnService
    workspace/       Workspace 身份与 Runtime
    state/           本地 SQLite 权威状态、迁移与 Repository
    database/        可选 PostgreSQL 连接、旧 Schema 和迁移支持
    context/         短期上下文管理
    memory/          消息归档和长期记忆
    tools/           Workspace 工具
    telemetry/       结构化观测事件、广播与持久化
    hooks/           旧事件导入路径兼容层
```

架构文档：

- [CLI 架构](/docs/architecture/cli-architecture.md)
- [Core 架构](/docs/architecture/core-architecture.md)
- [Agent 执行架构](/docs/architecture/agent-execution-architecture.md)
- [本地数据库设计与一致性机制](/docs/architecture/database-state-and-consistency.md)
- [记忆管理与加载机制](/docs/architecture/memory-management.md)
- [Event 系统设计与维护指南](/docs/architecture/event-system.md)
- [非功能性需求](/docs/quality/non-functional-requirements.md)
- [非功能性测试与验收方案](/docs/quality/non-functional-testing.md)
- [Workspace 隔离与数据库迁移](/docs/decisions/workspace-isolation-and-migration.md)
- [配置参数参考](/docs/reference/configuration-reference.md)
- [配置、领域常量与 Prompt 管理边界](/docs/decisions/configuration-and-domain-constants.md)

## 测试

```shell
python -B -m unittest discover -s tests -v
```

默认测试不要求启动可选 PostgreSQL。需要验证真实 PostgreSQL 读写时，显式运行：

```powershell
$env:LEARN_AGENT_RUN_POSTGRES_INTEGRATION_TESTS = "1"
python -B -m unittest tests.optional.test_memory_store -v
```

测试目录分类、运行方式和新增测试归属规则见
[测试结构与运行指南](/docs/quality/testing-guide.md)；性能与可靠性验收规则见
[非功能测试文档](/docs/quality/non-functional-testing.md)。

数据库集成测试需要先启动 PostgreSQL：

```shell
docker compose up -d postgres
```
