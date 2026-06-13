# Learn LangChain Agent

架构与运行机制：

- [本地优先 Session 状态](docs/local-first-session-state.md)
- [本地数据库设计与一致性机制](docs/database-state-and-consistency.md)
- [可恢复执行与预算控制](docs/resumable-execution.md)
- [PostgreSQL 到本地状态迁移](docs/local-state-migration.md)
- [本地优先优化的原因、风险与设计审查](docs/local-first-rationale-and-review.md)
- [最终响应、后台维护与 Checkpoint 一致性](docs/response-finalization-and-checkpoint-consistency.md)
- [配置、领域常量与 Prompt 管理边界](docs/configuration-and-domain-constants.md)

这是一个基于 LangChain、LangGraph 和本地 SQLite 状态库的 coding agent。

项目采用双进程结构：

- `learn-agent`：前台 CLI，负责用户交互和流式展示。
- `learn-agent-core`：后台 Core daemon，负责 Agent、工具、上下文和记忆。
- SQLite：保存 Workspace、Session、完整消息、长期记忆和可恢复执行状态，是本地业务状态的权威来源。
- PostgreSQL：作为可选的迁移来源、Telemetry Sink 和未来查询投影，不参与普通对话的必要提交。

## 快速开始

前置条件：

- Python 3.11 或更高版本。
- Docker 与 Docker Compose。
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

开发环境默认数据库账号和密码均为 `postgres`。非本地环境必须修改密码。

### 3. 启动 PostgreSQL

```shell
docker compose up -d postgres
docker compose ps
```

Compose 使用 `pgvector/pgvector:pg17`，数据保存在 Docker named volume
`learn_agent_postgres_data` 中，并且只将数据库端口绑定到 `127.0.0.1`。该名称是 Compose
文件中的逻辑名，实际 Docker 卷名通常带有 Compose 项目前缀。

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

停止服务：

```shell
learn-agent stop
docker compose stop postgres
```

Core 首次启动时会自动创建数据库 Schema。数据库未启动、凭据错误或检测到需要显式迁移的旧
Schema 时，Core 会拒绝启动。

## 配置方式

部署相关值通过 `.env` 或操作系统环境变量配置，不需要修改源码。

常用变量：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `LEARN_AGENT_LLM_API_KEY` | 空 | OpenAI 兼容模型 API 密钥 |
| `LEARN_AGENT_LLM_BASE_URL` | 空 | OpenAI 兼容 API 地址；留空时使用客户端默认地址 |
| `LEARN_AGENT_MODEL` | `deepseek-v4-flash` | 模型名称 |
| `LEARN_AGENT_DB_HOST` | `127.0.0.1` | PostgreSQL 地址 |
| `LEARN_AGENT_DB_PORT` | `5432` | PostgreSQL 端口 |
| `LEARN_AGENT_DB_NAME` | `learn_agent` | 数据库名 |
| `LEARN_AGENT_DB_USER` | `postgres` | 数据库用户 |
| `LEARN_AGENT_DB_PASSWORD` | `postgres` | 数据库密码 |
| `LEARN_AGENT_DATABASE_URL` | 空 | 可替代全部分项数据库配置 |
| `LEARN_AGENT_ENV_FILE` | 用户配置目录下 `.env` | 显式指定 Core 配置文件 |
| `LEARN_AGENT_RUNTIME_DIR` | 用户状态目录 | 显式指定 daemon 运行目录 |
| `LEARN_AGENT_CORE_PORT` | `18765` | CLI 与 Core 使用的本地 TCP 端口 |

完整部署方式、已有 Docker 数据目录复用、故障排查和安全边界见
[部署指南](docs/deployment.md)。

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
  core/
    app.py           依赖组装与生命周期管理
    bus/             JSON-RPC 验证、鉴权和路由
    transport/       TCP 与 NDJSON 传输
    agent/           LangGraph 和 AgentTurnService
    workspace/       Workspace 身份与 Runtime
    database/        Schema、SQL、连接和迁移
    context/         短期上下文管理
    memory/          消息归档和长期记忆
    tools/           Workspace 工具
    telemetry/       结构化观测事件、广播与持久化
    hooks/           旧事件导入路径兼容层
```

架构文档：

- [CLI 架构](docs/cli-architecture.md)
- [Core 架构](docs/core-architecture.md)
- [Agent 执行架构](docs/agent-execution-architecture.md)
- [本地数据库设计与一致性机制](docs/database-state-and-consistency.md)
- [记忆管理与加载机制](docs/memory-management.md)
- [Event 系统设计与维护指南](docs/event-system.md)
- [非功能性需求](docs/non-functional-requirements.md)
- [非功能性测试与验收方案](docs/non-functional-testing.md)
- [Workspace 隔离与数据库迁移](docs/workspace-isolation-and-migration.md)
- [配置、领域常量与 Prompt 管理边界](docs/configuration-and-domain-constants.md)

## 测试

```shell
python -B -m unittest discover -s tests -v
```

数据库集成测试需要先启动 PostgreSQL：

```shell
docker compose up -d postgres
```
