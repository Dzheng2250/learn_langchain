# Learn LangChain Agent

这是一个基于 LangChain、LangGraph 和 PostgreSQL 的本地学习型 Agent。

项目采用双进程结构：

- `learn-agent`：前台 CLI，只负责用户交互和流式展示。
- `learn-agent-core`：后台 Core daemon，负责 Agent、工具、上下文和记忆。
- CLI 与 Core 使用本机 TCP、NDJSON 和 JSON-RPC 2.0 通信。

## 前置依赖

- Python >= 3.11
- PostgreSQL 17（需要 pgvector 扩展，用于全文检索记忆）
- Docker（推荐，用于快速启动数据库）

## 快速开始

### 1. 克隆项目并安装

```bash
git clone <repo-url> && cd learn_langchain
pip install -e .
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，至少填入 ALIYUN_API_KEY
```

必需的环境变量：

| 变量 | 说明 |
|------|------|
| `ALIYUN_API_KEY` | 阿里云百炼 API Key |
| `ALIYUN_BASE_URL` | 阿里云百炼兼容 OpenAI 的 Base URL |

可选环境变量（数据库相关，默认值适用于 docker-compose）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LEARN_AGENT_DB_HOST` | `localhost` | 数据库主机 |
| `LEARN_AGENT_DB_PORT` | `5432` | 数据库端口 |
| `LEARN_AGENT_DB_NAME` | `learn_agent` | 数据库名 |
| `LEARN_AGENT_DB_USER` | `postgres` | 数据库用户 |
| `LEARN_AGENT_DB_PASSWORD` | `postgres` | 数据库密码 |
| `LEARN_AGENT_MODEL` | `deepseek-v4-flash` | 使用的模型 |

### 3. 启动 PostgreSQL 数据库

**方式一：使用 docker-compose（推荐）**

```bash
docker compose up -d
```

**方式二：手动启动 Docker 容器**

```bash
docker run -d -p 5432:5432 \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=learn_agent \
  -v learn_agent_pgdata:/var/lib/postgresql/data \
  --name learn_agent_pg pgvector/pgvector:pg17
```

**方式三：使用本地 PostgreSQL**

确保已安装 PostgreSQL 17 和 pgvector 扩展，然后创建数据库：

```sql
CREATE DATABASE learn_agent;
```

并通过环境变量配置连接信息。

### 4. 初始化密钥配置

```bash
learn-agent-core init-user-config --from-env .env
```

### 5. 启动并使用

```bash
# 启动 Core daemon
learn-agent start

# 查看状态
learn-agent status

# 交互式对话
learn-agent chat

# 单次提问
learn-agent chat --session default "查看当前项目结构"

# 停止 Core daemon
learn-agent stop
```

## 数据库说明

本项目使用 PostgreSQL 存储会话、消息、长期记忆和 Agent 事件。数据库 schema 由 Core daemon 在首次启动时自动创建（表结构见 `src/core/database/sql/schema.sql`）。

数据库连接信息默认指向本地 `localhost:5432`，数据库名 `learn_agent`，可通过环境变量覆盖（见上方表格）。

### 旧版数据库迁移

如果从旧版本升级，Core daemon 会检测到旧 schema 并拒绝启动，提示运行迁移命令：

```bash
# 先停止 daemon
learn-agent stop

# 预览迁移（dry-run）
learn-agent-core migrate-workspace \
  --workspace /path/to/your/project \
  --keep-session default

# 执行迁移（会自动备份数据库）
learn-agent-core migrate-workspace \
  --workspace /path/to/your/project \
  --keep-session default \
  --apply
```

迁移前会自动创建完整的 `pg_dump` 备份，备份文件位于用户数据目录的 `backups/` 下，可通过 `pg_restore` 恢复。

## CLI 配置加载

CLI 每次启动时首先调用 `CliConfig.load()`，从 `src/config/settings.py` 读取并验证：

```text
CORE_HOST
CORE_PORT
CORE_CONNECT_TIMEOUT_SECONDS
DEFAULT_SESSION_ID
```

运行目录由 `platformdirs` 解析为用户级目录，可通过
`LEARN_AGENT_RUNTIME_DIR` 显式覆盖。Core 从用户级 `.env` 加载密钥配置，可通过
`LEARN_AGENT_ENV_FILE` 显式覆盖。CLI 只读取通信、运行目录和默认会话配置；模型、
数据库、工具等业务配置由 Core daemon 读取。

## 结构

```text
src/
  config/          CLI 与 Core 共用的非敏感运行配置
  ipc/             CLI 与 Core 共用的 JSON-RPC 模型和本地凭据
  cli/
    commands/      独立 CLI 命令
    main.py        配置加载、命令注册和统一错误处理
    client.py      JSON-RPC 客户端
    daemon.py      daemon 生命周期管理
  core/
    main.py        Core daemon 命令入口
    app.py         依赖组装与生命周期管理
    bus/           JSON-RPC 验证、鉴权和路由
    handlers/      RPC 与业务服务适配
    transport/     TCP 与 NDJSON 传输
    agent/         LangGraph 定义和 AgentTurnService
    workspace/     Workspace 身份、Repository 与 Runtime
    database/      Schema、SQL、连接和显式迁移
    context/       短期上下文管理与压缩
    memory/        会话归档和长期记忆
    tools/         工具实现与 ToolNode wrapper
    hooks/         结构化事件系统
```

详细通信原理见 [双进程与JSON-RPC设计说明.md](双进程与JSON-RPC设计说明.md)。

CLI 优化方案和扩展规则见 [docs/cli-architecture.md](docs/cli-architecture.md)。

CoreApp、Handlers 与 Transport 架构见 [docs/core-architecture.md](docs/core-architecture.md)。

Workspace 隔离、记忆策略和数据库迁移见
[docs/workspace-isolation-and-migration.md](docs/workspace-isolation-and-migration.md)。

实施后的独立代码审查见
[docs/workspace-isolation-review.md](docs/workspace-isolation-review.md)。

## 测试

运行完整测试：

```bash
python -B -m unittest discover -s tests -v
```
