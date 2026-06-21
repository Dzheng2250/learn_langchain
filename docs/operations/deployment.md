# 部署指南

> 文档状态：Current
> 权威范围：首次安装、配置和基础设施部署
> 维护触发：安装方式、依赖、配置加载或部署模型变化

## 本文负责

- 首次安装、用户级配置、基础设施启动和最小可用验证。

## 本文不负责

- 不维护所有配置字段；见配置参考。
- 不说明内部架构或开发环境工作流。

> 配置参数默认值、单位和调整风险见 [`/docs/reference/configuration-reference.md`](/docs/reference/configuration-reference.md)。

日常启动、Session 排障和维护任务处理见[运维 Runbook](/docs/operations/runbook.md)；
状态保护见[备份与恢复](/docs/operations/backup-and-restore.md)。

## 推荐部署模型

推荐使用以下组合：

```text
宿主机
  ├── learn-agent CLI
  ├── learn-agent-core daemon
  └── Docker（按需）
       ├── PostgreSQL + pgvector（可选）
       └── Agent 命令执行沙箱（使用容器命令工具时需要）
```

项目没有默认将整个 Agent 放入容器，原因是 coding agent 需要访问当前宿主机 Workspace，并且
命令工具还会调用 Docker 创建短生命周期沙箱。将 Core 放入容器会额外引入：

- Workspace 路径映射与权限差异。
- Docker-in-Docker 或宿主 Docker socket 暴露。
- 用户级 daemon 的端口、token 和运行目录映射。
- Windows、Linux 和 macOS 不一致的宿主路径语义。

因此，当前 `compose.yaml` 只负责可选 PostgreSQL 基础设施。普通对话状态保存在本地 SQLite，
不需要先启动 PostgreSQL。

## 从零部署

### 前置条件

- Python 3.11 或更高版本。
- Docker Engine 或 Docker Desktop。使用容器命令工具、PostgreSQL 可选能力或迁移时需要。
- Docker Compose v2，即 `docker compose` 命令。启动项目提供的 PostgreSQL 时需要。
- OpenAI 兼容模型 API。仅验证基础设施时可以暂不配置。

### 创建配置

创建不提交到 Git 的 `.env`：

```shell
python -c "from shutil import copyfile; copyfile('.env.example', '.env')"
```

需要真实 Agent 回答时配置 `LEARN_AGENT_LLM_API_KEY`，并按服务需要设置
`LEARN_AGENT_LLM_BASE_URL`。仅验证基础设施时可以保持两者为空。`.env.example` 中的数据库
默认值与 `compose.yaml` 一致，因此本地开发可以直接启动。

项目根目录的 `.env` 会被 Docker Compose 自动读取，用于创建数据库容器；执行
`init-user-config` 后，同一份配置会被复制到用户级目录，供任意工作目录启动的 CLI 与 Core
读取。

### 可选：启动 PostgreSQL

```shell
docker compose up -d postgres
docker compose ps
docker compose logs postgres
```

Compose 提供：

- PostgreSQL 17。
- pgvector 镜像，为未来向量检索保留扩展能力。
- `pg_isready` 健康检查。
- Compose 逻辑名为 `learn_agent_postgres_data` 的 Docker named volume。
- 仅绑定到 `127.0.0.1` 的数据库端口。

PostgreSQL 当前用于旧数据迁移、可选事件 Sink 和未来查询投影。普通 Session、消息、长期记忆、
Execution 与维护任务以本地 `state.db` 为准，因此未启用 PostgreSQL 能力时可以跳过本节。

普通 Core 启动不会初始化 PostgreSQL 业务 Schema。PostgreSQL 当前主要用于保留旧数据、执行
显式迁移，以及在已有 `agent_events` 表时作为可选 Event Sink。当前业务尚未使用向量字段，
因此无需手动执行 `CREATE EXTENSION vector`。

Compose 默认会为卷名增加项目名前缀。可通过 `docker volume ls` 查看实际名称，不应根据逻辑名
直接删除或搬运卷。

### 安装与启动应用

```shell
python -m pip install -e .
learn-agent-core init-user-config --from-env .env
learn-agent start
learn-agent status
```

`init-user-config` 将 `.env` 复制到用户级配置目录。之后可从任意工作目录管理同一个 daemon。

### 无 LLM 配置诊断模式

未设置 `LEARN_AGENT_LLM_API_KEY` 时，`learn-agent chat` 不会尝试构造或调用模型。Core 会：

1. 接收并验证 JSON-RPC 请求。
2. 解析或创建当前 Workspace 与 Session。
3. 读取 Session，验证本地 SQLite Schema 与读写链路。
4. 发送流式 token 与完成事件。
5. 返回 `stop_reason=llm_not_configured`。

这条路径用于确认 CLI/Core 通信、daemon、本地 SQLite Schema、Workspace 隔离、Session 和事件链路
正常工作。它不会写入对话历史或递增 `turn_index`，因此重复诊断不会影响首次真实 LLM Turn 的
bootstrap memory。它不验证模型网络连接、工具调用、长期记忆提取或上下文总结。

配置模型后重新同步用户配置并重启 Core：

```shell
learn-agent stop
learn-agent-core init-user-config --from-env .env --force
learn-agent start
```

通用变量：

```dotenv
LEARN_AGENT_LLM_API_KEY=your-api-key
LEARN_AGENT_LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
LEARN_AGENT_MODEL=your-model
```

旧 `ALIYUN_API_KEY` 与 `ALIYUN_BASE_URL` 仅作为兼容回退，已弃用。若新旧变量同时存在，通用变量
优先。

## 可选 PostgreSQL 配置

支持两种配置方式。

### 分项变量

```dotenv
LEARN_AGENT_DB_HOST=127.0.0.1
LEARN_AGENT_DB_PORT=5432
LEARN_AGENT_DB_NAME=learn_agent
LEARN_AGENT_DB_USER=postgres
LEARN_AGENT_DB_PASSWORD=replace-with-a-strong-password
```

### 单一连接 URL

```dotenv
LEARN_AGENT_DATABASE_URL=postgresql://postgres:password@127.0.0.1:5432/learn_agent
```

设置 `LEARN_AGENT_DATABASE_URL` 后，它优先于分项变量。PostgreSQL Event Sink、旧数据迁移和
备份使用同一数据库配置来源；普通对话的本地 SQLite 提交不使用该连接。

连接 URL 中的用户名和密码若包含 `@`、`:`、`/` 等保留字符，必须先进行 URL 编码；不确定时
优先使用分项变量。

## 复用已有 Docker 数据

已有部署示例：

```text
container: pgvector2
database: robot
bind mount: E:\docker\pgvector2\data
```

不要直接执行 `docker compose up` 并期望 named volume 自动读取该目录。Docker named volume
和已有 bind mount 是两套独立数据位置。

### 方案 A：继续使用已有容器

这是风险最低的方案。保持已有容器运行，并在 `.env` 中配置实际数据库：

```dotenv
LEARN_AGENT_DB_HOST=127.0.0.1
LEARN_AGENT_DB_PORT=5432
LEARN_AGENT_DB_NAME=robot
LEARN_AGENT_DB_USER=postgres
LEARN_AGENT_DB_PASSWORD=postgres
LEARN_AGENT_DB_CONTAINER=pgvector2
```

随后重新初始化用户级配置并重启 Core：

```shell
learn-agent stop
learn-agent-core init-user-config --from-env .env --force
learn-agent start
```

### 方案 B：让 Compose 继续使用已有目录

创建仅属于本机、不要提交到 Git 的 `compose.override.yaml`：

```yaml
services:
  postgres:
    volumes:
      - E:/docker/pgvector2/data:/var/lib/postgresql/data
```

注意：

- Windows Compose 路径推荐使用正斜杠。
- 必须先停止原容器，避免两个 PostgreSQL 进程同时访问同一数据目录。
- 数据目录中的 PostgreSQL 主版本必须与 `pgvector/pgvector:pg17` 一致。
- 切换前应先使用 `pg_dump` 创建备份。

如果需要最可控的迁移，使用 `pg_dump` 从旧容器导出，再导入新的 Compose 数据卷，而不是直接
搬运数据库文件。

## 常用数据库命令

启动：

```shell
docker compose up -d postgres
```

查看状态和健康检查：

```shell
docker compose ps
```

查看日志：

```shell
docker compose logs -f postgres
```

停止但保留数据：

```shell
docker compose stop postgres
```

删除容器但保留 named volume：

```shell
docker compose down
```

删除容器和数据卷：

```shell
docker compose down -v
```

最后一条命令会永久删除 Compose 管理的数据库数据，不应用于包含重要数据的环境。

## 配置加载顺序

CLI 与 Core 都会先加载同一份用户级配置。Core 启动顺序：

```text
读取 LEARN_AGENT_ENV_FILE 或用户级 .env
  -> 导入 settings.py 并应用环境变量覆盖
  -> 创建数据库连接池、事件 sink 和 Agent 服务
  -> 初始化或校验数据库 Schema
  -> 启动本地 TCP 服务
```

环境变量优先级：

```text
操作系统已有环境变量
  > 用户级 .env
  > settings.py 中的开发默认值
```

`load_dotenv(..., override=False)` 不会覆盖进程启动前已经设置的环境变量。

注意项目根目录 `.env` 与用户级 `.env` 的职责：

- Docker Compose 自动读取当前项目目录的 `.env`。
- CLI/Core 读取用户级 `.env`，从而允许在任意工作目录访问同一 daemon。
- 修改项目 `.env` 后，需要重新执行 `init-user-config --force` 并重启 Core，才能同步到用户级配置。

## 安全注意事项

- `.env` 包含 API 密钥和数据库密码，已被 `.gitignore` 排除。
- `.env.example` 只能包含占位符或本地开发默认值。
- Compose 默认只将 PostgreSQL 暴露到 `127.0.0.1`。
- 非本地环境必须修改默认数据库密码。
- 不要将 Docker socket 挂载到不可信容器。
- 执行数据库迁移或切换数据目录前必须备份。
- 不要同时让多个 PostgreSQL 容器访问同一个物理数据目录。

## 当前部署边界

- `compose.yaml` 只管理 PostgreSQL，不负责容器化 CLI/Core。
- 当前目标是单机、本地用户级 daemon，不提供公网服务部署方案。
- 当前没有 Kubernetes、反向代理、TLS、数据库高可用或自动备份调度。
- 数据库 Schema 会自动初始化，但旧结构升级仍必须使用显式迁移命令。
- Docker named volume 适合快速本地部署；重要数据仍应使用 `pg_dump` 定期备份。

## 故障排查

### Core 启动失败并提示数据库连接错误

```shell
docker compose ps
docker compose logs postgres
```

确认用户级 `.env` 与 Compose 使用相同的数据库名、用户、密码和端口。

### Compose 报告端口 5432 已被占用

说明已有 PostgreSQL 或容器正在监听该端口。可以继续使用现有数据库，或在 `.env` 中设置其他
端口：

```dotenv
LEARN_AGENT_DB_PORT=55432
```

然后重新执行：

```shell
docker compose up -d postgres
learn-agent-core init-user-config --from-env .env --force
```

### Compose 报告容器名称冲突

修改：

```dotenv
LEARN_AGENT_DB_CONTAINER=learn-agent-postgres-local
```

或停止并删除不再使用的同名容器。删除容器前先确认其中的数据是否已经持久化和备份。
