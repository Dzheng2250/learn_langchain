# CLI 架构

> 文档状态：Current
> 权威范围：CLI 命令模块、配置、客户端和 daemon 生命周期管理内部设计
> 维护触发：CLI 目录、命令注册、客户端或 daemon 管理方式变化
> 配置默认值与环境变量名称见 [`/docs/reference/configuration-reference.md`](/docs/reference/configuration-reference.md)。

> 当前 CLI 在每次 `chat` 时识别最近 Git 根目录，并将 `workspace_root` 与
> `session_name` 发送给用户级 Core daemon。详细设计见
> [`/docs/decisions/workspace-isolation-and-migration.md`](/docs/decisions/workspace-isolation-and-migration.md)。

## 本文负责

- CLI 命令模块、配置对象、同步 RPC client、daemon 管理和终端渲染的内部职责。
- CLI 模块之间允许和禁止的依赖方向。
- CLI 异常如何转换为用户可理解的错误。

## 本文不负责

- 不维护完整命令和参数清单；见 [CLI 命令参考](/docs/api/cli-reference.md)。
- 不维护 RPC、事件或鉴权字段；见 `/docs/api/`。
- 不解释双进程方案的历史取舍；见 [CLI / Core 设计决策](/docs/decisions/cli-core-json-rpc.md)。
- 不解释 Core 内部 Agent、状态库或工具实现；见 `/docs/architecture/`。

## 目标

CLI 是 Core daemon 的前台客户端，只负责命令解析、生命周期管理、请求发送和结果展示。CLI 不直接执行 Agent、工具、记忆或数据库业务。

本次优化解决以下问题：

1. `cli/main.py` 同时承担命令注册、聊天循环和命令执行，新增命令会持续膨胀。
2. CLI 直接导入 `core.bus`，形成前台对后台实现细节的反向依赖。
3. JSON-RPC 模型和 daemon 凭据文件同时被 CLI/Core 使用，却放在 Core 内部。
4. 缺少 `python -m src.cli` 标准模块入口。

## 设计决策

### 命令模式

每个 CLI 命令是独立模块，负责注册自身参数并提供 handler：

```text
src/cli/commands/
  start.py
  stop.py
  status.py
  chat.py
  session.py
```

`src/cli/main.py` 只负责：

```text
加载并验证 CLI 配置
  -> 创建 argparse parser
  -> 注册命令
  -> 解析参数
  -> 调用选中的 handler
  -> 统一处理 CLI 错误
```

新增命令时只需要增加命令模块并在命令注册表中登记，不需要向 `main.py` 添加业务分支。

### 中立 IPC 层

CLI 和 Core 都依赖 IPC 协议，但二者不应互相依赖：

```text
CLI -> IPC <- Core
```

共享能力放在：

```text
src/ipc/
  models.py   JSON-RPC 请求、响应、参数和事件模型
  auth.py     daemon token 与运行时文件路径
```

Core 内部的 `bus` 继续负责路由与 server，但协议模型不属于 Core 私有实现。

### 配置加载

CLI 入口会先调用 `load_user_environment()`，加载 `LEARN_AGENT_ENV_FILE` 指定的文件或用户级
`.env`；随后才导入 `settings.py` 并调用 `CliConfig.load()` 验证通信配置。验证完成后，同一个
不可变配置对象传递给命令、RPC client 和 daemon 管理器。

CLI 只读取：

```text
Core 地址和端口
连接超时
daemon 启动和停止超时
运行时目录
默认 session
```

模型、数据库和工具配置由 Core daemon 自行读取。

## 最终结构

```text
src/
  cli/
    __main__.py
    main.py
    config.py
    client.py
    daemon.py
    render.py
    commands/
      chat.py
      start.py
      stop.py
      status.py

  ipc/
    models.py
    auth.py

  core/
    bus/
      router.py
      server.py
      framing.py
```

## 调用流程

完整的 CLI、RPC、Agent、工具、记忆和数据库时序见
[Agent 完整数据流动示意图](/docs/architecture/agent-execution-call-chain.md#完整数据流动示意图)。

```text
learn-agent chat
  -> CliConfig.load()
  -> main 注册并解析命令
  -> commands.chat.run()
  -> CoreClient.request("agent.chat")
  -> IPC JSON-RPC
  -> Core daemon
  -> 流式 agent.event
  -> CLI render
```

## 扩展规则

1. 新 CLI 命令放入 `cli/commands/`，不把业务逻辑写回 `main.py`。
2. CLI 与 Core 共用的 wire model 放入 `ipc/`。
3. CLI 不得导入 `core.agent`、`core.memory` 或 `core.tools`。
4. `daemon.py` 只管理 Core 进程生命周期，不负责命令行输出。
5. `render.py` 只负责展示，不发送 RPC。
6. 命令 handler 返回进程退出码，错误由 `main.py` 统一展示。

## 设计取舍

CLI / Core 双进程、命令模块化、独立 IPC 契约和 daemon 生命周期管理的选择原因、替代方案与代价，
统一记录在 [CLI / Core 双进程与 JSON-RPC 设计决策](/docs/decisions/cli-core-json-rpc.md)。

本文只记录当前 CLI 内部结构，不重复维护设计优缺点清单。

## 当前功能边界

当前支持能力由以下权威文档维护：

- CLI 命令、参数和退出行为：[CLI 命令参考](/docs/api/cli-reference.md)。
- TCP、NDJSON、鉴权和连接生命周期：[Core IPC 协议](/docs/api/ipc-protocol.md)。
- RPC 方法、参数和结果：[RPC 方法参考](/docs/api/rpc-reference.md)。
- Agent 流式通知：[Agent 流式事件参考](/docs/api/streaming-events.md)。
- 配置变量和默认值：[配置参数参考](/docs/reference/configuration-reference.md)。
- 安全措施和限制：[安全模型](/docs/architecture/security-model.md)。
- 尚未实现能力：[路线图与已知限制](/docs/product/roadmap-and-known-limitations.md)。

CLI 架构文档不再复制上述清单，避免命令、RPC 或配置变化后出现多个互相冲突的版本。

## 模块职责边界

```text
cli/main.py
    负责配置加载、命令注册、参数解析和统一错误处理。

cli/commands/
    负责各命令参数和用户工作流。

cli/client.py
    负责同步 JSON-RPC 请求与流式通知接收。

cli/daemon.py
    负责 Core 后台进程生命周期。

cli/render.py
    负责终端展示。

ipc/
    负责 CLI、TUI 与 Core 共享的协议模型和本地凭据。
```

禁止的依赖方向：

```text
CLI -> Core Agent / Memory / Tools
IPC -> CLI
IPC -> Core Agent
Render -> RPC Client
Tool -> CLI
```
## 异常与容错策略

CLI 不直接向用户暴露 socket、JSON、Pydantic 或文件系统异常。底层异常会先转换为稳定的 CLI 领域异常，再由入口或交互命令决定如何展示和恢复。

当前异常分类：

```text
ConfigurationError
    CLI 配置无效，退出码为 2。

CoreUnavailableError
    token 缺失、拒绝连接、连接超时或无法访问 Core。

CoreConnectionInterruptedError
    请求执行过程中连接被关闭或重置，响应可能不完整。

CoreProtocolError
    Core 返回无效 JSON、错误结构或不兼容响应。

CoreAuthenticationError
    Core 拒绝当前 token。

CoreRequestError
    Core 正常接收请求，但拒绝或执行失败。

DaemonLifecycleError
    daemon 运行目录、启动或关闭流程失败。

CliRenderError
    流式结果无法写入终端或渲染回调失败。
```

用户体验规则：

1. 预期异常只显示简洁错误和可执行的 `Hint`，不输出 traceback。
2. `chat` 交互模式中的单轮失败不会结束整个交互会话，用户可以修复 Core 后继续输入。
3. 流式连接中断时明确提示已显示响应可能不完整，避免用户误认为任务完整结束。
4. `status` 只把“Core 不可用”解释为未运行；鉴权或协议错误会明确暴露，避免误判后覆盖运行中的 daemon token。
5. `Ctrl+C` 返回退出码 `130` 并显示 `Cancelled`。
6. 未分类异常由主入口兜底，避免直接打印 traceback，但会显示异常类型用于诊断。

当前容错边界：

- 不自动重试 `agent.chat`，因为请求可能已在 Core 中执行，自动重试可能造成重复工具调用或重复写入。
- 不支持连接恢复和事件续传；中断后需要用户检查 daemon 日志。
- 默认不强制终止关闭超时的 daemon，只返回明确错误，并提示用户检查日志。
- `learn-agent stop --force` 是显式人工逃生口：当同步工具或底层系统调用卡住导致 daemon 无法完成
  优雅关闭时，CLI 会在优雅关闭超时后根据 PID 终止 daemon 进程并清理 runtime 文件。该能力不是
  工具级取消机制；它会中断整个 Core 进程，允许丢失尚未 flush 的 best-effort Trace/Telemetry。
- Core 内部任务失败通过 RPC/流式事件返回，CLI 不尝试替代 Core 恢复业务状态。

## 后续方向

- 将项目包名从通用的 `src` 调整为正式包名，例如 `learn_agent`。
- 将内部策略常量逐步收敛为严格配置模型；仅在确有层次化配置需求时引入 TOML。
- 为 TUI 复用 `ipc` 模型和异步 RPC client（`AsyncCoreClient`）。详见 [TUI 架构](/docs/architecture/tui-architecture.md)。
- Core 已拆分 `bus`、`handlers` 与 `transport`，并由 `CoreApp` 统一组装和管理生命周期。详见
  [`Core 架构`](/docs/architecture/core-architecture.md)。
