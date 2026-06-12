# Learn LangChain Agent

这是一个基于 LangChain、LangGraph 和 PostgreSQL 的本地 coding agent。

项目采用双进程结构：

- `learn-agent`：前台 CLI，负责用户交互和流式展示。
- `learn-agent-core`：后台 Core daemon，负责 Agent、工具、上下文和记忆。
- PostgreSQL：保存 Workspace、Session、完整消息、长期记忆和观测事件。

## 快速开始

前置条件：

- Python 3.11 或更高版本。
- Docker 与 Docker Compose。
- 可用的 OpenAI 兼容模型 API。

### 1. 安装项目

```shell
python -m pip install -e .
```

### 2. 创建本地配置

以下命令在 Windows、Linux 和 macOS 均可运行：

```shell
python -c "from shutil import copyfile; copyfile('.env.example', '.env')"
```

编辑 `.env`，至少设置：

```dotenv
ALIYUN_API_KEY=your-api-key
ALIYUN_BASE_URL=https://your-openai-compatible-endpoint/v1
```

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
| `ALIYUN_API_KEY` | 无 | 模型 API 密钥 |
| `ALIYUN_BASE_URL` | 无 | OpenAI 兼容 API 地址 |
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
    hooks/           结构化观测事件
```

架构文档：

- [CLI 架构](docs/cli-architecture.md)
- [Core 架构](docs/core-architecture.md)
- [Agent 执行架构](docs/agent-execution-architecture.md)
- [Workspace 隔离与数据库迁移](docs/workspace-isolation-and-migration.md)

## 测试

```shell
python -B -m unittest discover -s tests -v
```

数据库集成测试需要先启动 PostgreSQL：

```shell
docker compose up -d postgres
```
