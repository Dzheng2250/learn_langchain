# Learn LangChain Agent

这是一个基于 LangChain、LangGraph 和 PostgreSQL 的本地学习型 Agent。

项目采用双进程结构：

- `learn-agent`：前台 CLI，只负责用户交互和流式展示。
- `learn-agent-core`：后台 Core daemon，负责 Agent、工具、上下文和记忆。
- CLI 与 Core 使用本机 TCP、NDJSON 和 JSON-RPC 2.0 通信。

## 安装

```powershell
D:\app\anaconda\envs\agent_learn\python.exe -m pip install -e .
```

## 使用

首次迁移已有项目配置时，将密钥配置显式复制到用户级配置目录：

```powershell
learn-agent-core init-user-config --from-env .env
```

启动 Core daemon：

```powershell
learn-agent start
```

查看状态：

```powershell
learn-agent status
```

交互式对话：

```powershell
learn-agent chat
```

单次提问：

```powershell
learn-agent chat --session default "查看当前项目结构"
```

CLI 会将当前目录最近的 Git 根目录识别为 Workspace；非 Git 目录使用当前目录。
同一个用户级 daemon 可以从任意目录访问，但 Session、记忆、工具和 Skill 严格绑定
当前 Workspace。可使用 `--workspace <path>` 显式指定 Workspace。

停止 Core daemon：

```powershell
learn-agent stop
```

未安装命令入口时，也可以使用兼容入口：

```powershell
D:\app\anaconda\envs\agent_learn\python.exe agent_loop.py start
```

`agent_loop.py` 已不再执行 Agent，只负责转发到新 CLI。无参数运行时默认进入 `chat`，并输出兼容入口提示。

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

```powershell
D:\app\anaconda\envs\agent_learn\python.exe -B -m unittest discover -s tests -v
```
