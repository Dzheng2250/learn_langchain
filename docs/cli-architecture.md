# CLI 架构优化方案

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

CLI 每次启动时首先调用 `CliConfig.load()`，读取并验证通信配置。验证完成后，同一个不可变配置对象传递给命令、RPC client 和 daemon 管理器。

CLI 只读取：

```text
Core 地址和端口
连接超时
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

## 设计优缺点

### 优点

#### 1. CLI 命令职责清晰

每个命令独立存放在 `cli/commands/`，参数定义和执行逻辑位于同一个模块中。

收益：

- 新增命令时不需要持续修改大型 `main.py`。
- `start`、`chat` 等命令可以独立测试。
- 阅读代码时可以直接定位到对应命令。
- 不同命令的依赖不会全部堆积到主入口。

相比在 `main.py` 中使用大量 `if/elif`，这种结构更适合命令数量持续增长的项目。

#### 2. CLI 与 Core 解耦

CLI 不再导入 `core.agent`、`core.memory`、`core.tools` 或 Core 私有 Bus 模型。

收益：

- CLI 只能通过公开 IPC 协议调用 Core，边界更加明确。
- 后续增加 TUI、Web 客户端时可以复用相同协议。
- Core 内部重构不会直接破坏 CLI。
- 更容易验证“任务只在 daemon 中执行”。

#### 3. IPC 协议成为独立契约

JSON-RPC 请求、响应和事件模型放在 `src/ipc/`，由 CLI 和 Core 共同依赖。

收益：

- 请求与响应结构只有一个定义来源。
- Pydantic 可以在发送端和接收端进行一致验证。
- 协议模型可以独立测试。
- 未来可以增加协议版本或为其他客户端生成文档。

#### 4. 配置加载时机明确

CLI 入口首先加载并验证配置，再执行任何命令。

收益：

- 配置错误会在连接或启动 daemon 前暴露。
- 同一个不可变配置对象贯穿命令、client 和 daemon 管理逻辑。
- 避免不同模块分别读取配置后产生不一致。
- 测试时可以直接构造配置对象。

#### 5. daemon 生命周期对用户更友好

CLI 提供 `start / status / stop`，用户不需要手动管理后台 Python 进程。

收益：

- 启动后通过 `core.ping` 确认服务真正可用。
- 停止时通过 JSON-RPC 请求 Core 正常关闭。
- PID、token 和日志文件集中存放。
- 后续可以增加日志查看、重启和诊断命令。

#### 6. 更适合测试

命令 handler、RPC client、IPC 模型和 Core server 都有明确边界。

收益：

- 命令测试可以 mock daemon/client，不需要调用真实 LLM。
- IPC 测试可以使用假的 Agent service。
- Core Agent 测试不依赖 CLI。
- 容易定位失败属于命令、协议、传输还是业务层。

### 缺点与代价

#### 1. 文件和抽象数量增加

简单功能被拆分为多个模块：

```text
main
command
client
ipc model
router
server
service
```

代价：

- 初学者需要理解更多层次才能追踪完整调用链。
- 对只有一两个命令的小型脚本而言可能过度设计。
- 修改一个跨层功能时可能需要同时调整多个文件。

因此文档、命名和依赖规则必须保持准确，否则模块拆分只会变成形式上的复杂度。

#### 2. IPC 增加运行复杂度

CLI 与 Core 不再是普通函数调用，而是两个独立进程。

代价：

- 需要处理端口占用、daemon 未启动、连接断开和超时。
- 需要管理 token、PID 和日志文件。
- 调试时需要同时观察 CLI 与 Core。
- 协议变更必须考虑客户端与 daemon 的兼容性。

#### 3. JSON-RPC 模型需要持续维护

公开协议成为稳定契约后，修改字段不能只考虑当前代码。

代价：

- 新增或修改 RPC 方法时需要同步更新模型、handler、client 和测试。
- 未来 CLI 与 daemon 版本不一致时，需要协议版本协商或兼容策略。
- 流式事件结构若缺少约束，会逐渐退化为不透明的 `dict`。

#### 4. 当前配置仍是代码配置

目前 `src/config/settings.py` 是统一来源，但它仍然是 Python 文件。

代价：

- 用户修改配置需要编辑项目源码。
- 不同环境之间的配置切换不够方便。
- 非敏感配置与业务常量仍集中在同一个文件中。

后续应考虑增加 TOML 配置和环境变量覆盖，但需要明确优先级，避免配置来源过多。

#### 5. daemon 管理仍是应用自行实现

当前 CLI 使用 `subprocess.Popen` 管理后台进程。

代价：

- 异常崩溃后可能留下旧 PID 或 token 文件。
- 操作系统重启后不会自动恢复。
- Windows、Linux 和 macOS 的后台进程行为存在差异。
- 不具备 systemd、Windows Service 等系统服务管理器的可靠性。

当前方案适合本地开发型 Agent。若未来作为长期稳定服务运行，应考虑接入操作系统服务管理器。

#### 6. CLI 命令注册仍需要手动维护

新增命令模块后，仍需在 `cli/commands/__init__.py` 中注册。

代价：

- 忘记注册时命令不会出现在 CLI 中。
- 命令很多时注册列表会增长。

现阶段显式注册更容易理解和检查。只有命令数量非常多时，才值得考虑插件式自动发现。

#### 7. 顶层包名 `src` 不够标准

当前导入路径为：

```python
from src.cli.client import CoreClient
```

代价：

- `src` 是目录布局概念，不适合作为正式 Python 包名。
- 安装后的公共导入路径缺少项目辨识度。
- 容易与其他项目或工具中的 `src` 包发生概念混淆。

长期应迁移为：

```text
src/learn_agent/
```

并使用：

```python
from learn_agent.cli.client import CoreClient
```

### 适用范围

当前设计适合以下情况：

- Agent 需要作为后台服务持续运行。
- 未来会同时支持 CLI、TUI 或其他客户端。
- CLI 命令和 RPC 方法会继续增加。
- 需要流式输出、会话管理、工具调用和安全边界。

如果项目只是一个一次性命令脚本，或者始终只有一个简单入口，则没有必要使用完整的双进程、IPC 和命令模块结构。

当前项目已经具备长期运行 Agent 的需求，因此这些额外复杂度是合理的，但后续应避免为了“看起来分层”而继续增加没有实际职责的抽象。

## 当前支持的功能边界

本节描述当前代码已经实现的能力，不代表未来规划。

### CLI 当前支持

CLI 当前提供四个命令：

```text
learn-agent start
learn-agent stop
learn-agent status
learn-agent chat
```

#### `start`

当前支持：

- 检查 Core daemon 是否已经运行。
- 创建 `.agent_runtime/` 运行目录。
- 生成随机本地鉴权 token。
- 使用独立后台进程启动 Core。
- 将 CLI 已验证的 host 和 port 传给 Core。
- 轮询 `core.ping`，确认 Core 真正启动成功。
- 将 Core 标准输出和错误写入 daemon 日志。

当前不支持：

- daemon 崩溃后自动重启。
- 操作系统启动时自动启动。
- 多个 Core 实例管理。
- 自定义 daemon 名称或 profile。
- 启动过程中展示结构化进度。

#### `status`

当前支持：

- 使用 token 调用 `core.ping`。
- 显示 Core 是否运行。
- 显示 Core uptime。

当前不支持：

- 显示 PID、监听地址、版本兼容状态或活跃任务数。
- 显示数据库、LLM、Docker 等依赖健康状态。
- 区分 token 错误、端口占用和 daemon 未运行。

#### `stop`

当前支持：

- 使用 JSON-RPC 调用 `core.shutdown`。
- 等待 daemon 停止响应。
- 清理 PID 和 token 文件。

当前不支持：

- 强制终止卡死进程。
- 指定关闭超时。
- 在关闭前列出或取消活跃任务。
- 操作系统信号与 JSON-RPC shutdown 的统一状态报告。

#### `chat`

当前支持：

- 使用默认 session 或 `--session` 指定会话。
- 单次提问：

  ```text
  learn-agent chat "问题"
  ```

- 交互式连续输入：

  ```text
  learn-agent chat
  ```

- 忽略空输入。
- 使用 `exit` 或 `quit` 离开交互模式。
- 展示 LLM token 流。
- 展示工具调用开始和结果步骤。
- 展示 Agent 错误。

当前不支持：

- 任务取消。
- 客户端断线后重新订阅流式结果。
- 聊天历史列表、会话创建、重命名或删除命令。
- Markdown 富文本、颜色、进度条或 TUI 展示。
- 上传文件、图片或其他多模态输入。
- 从 CLI 选择模型、工具权限或 Agent 类型。

### CLI 配置边界

CLI 当前只读取并验证：

```text
CORE_HOST
CORE_PORT
CORE_CONNECT_TIMEOUT_SECONDS
CORE_RUNTIME_DIR
DEFAULT_SESSION_ID
```

CLI 不负责读取或解释：

```text
模型名称和模型密钥
数据库连接配置
工具配置
记忆提取策略
上下文压缩策略
Hook sink 配置
Docker 沙盒配置
```

这些业务配置由 Core daemon 使用。

当前配置来源只有 `src/config/settings.py`。CLI 尚不支持命令行覆盖、TOML 配置文件或环境变量覆盖。

### RPC Client 当前支持

`CoreClient` 当前支持：

- 每次请求创建独立 TCP 连接。
- 为请求生成唯一 `request_id`。
- 自动读取本地 token 并加入请求参数。
- 发送单行 NDJSON JSON-RPC 请求。
- 验证 JSON-RPC 成功响应和错误响应。
- 接收与当前请求关联的 `agent.event` 流式通知。
- 将通知传给调用方提供的回调函数。

当前不支持：

- 长连接和连接池。
- 同一连接并发发送多个请求。
- 自动重试。
- 请求取消。
- 断线重连和事件续传。
- 客户端与 daemon 的协议版本协商。
- TLS 或远程网络连接。

### IPC 协议当前支持

当前 RPC 方法：

```text
core.ping
core.shutdown
agent.chat
```

当前服务端通知：

```text
agent.event
```

当前 `agent.event` 可携带：

```text
token
step
error
done
```

当前协议能力：

- JSON-RPC 2.0 请求、成功响应和错误响应。
- NDJSON 消息分帧。
- Pydantic 严格模型验证。
- 最大消息长度限制。
- 本地 token 鉴权。
- 标准 parse error、invalid request、method not found、invalid params 和 internal error。

当前协议限制：

- 事件内部 `data` 仍然是通用字典，没有为每类事件建立严格模型。
- 没有协议版本、功能协商或兼容性声明。
- 没有批量 JSON-RPC 请求。
- 不支持 JSON-RPC notification 形式的客户端请求。
- 仅支持本机回环地址。

### Core daemon 当前支持

Core 当前支持：

- 监听本机 TCP 回环地址。
- 验证 JSON-RPC 请求、参数和 token。
- 根据 RPC method 分发 handler。
- 使用 `AgentTurnService` 执行完整 Agent turn。
- 同一个 session 串行执行。
- 不同 session 并行执行。
- 流式返回 token、步骤、错误和完成事件。
- 保存会话上下文、消息和长期记忆。
- 接收正常 shutdown 请求。

当前不支持：

- Core 集群或多实例协调。
- 分布式 session 锁。
- 任务队列、任务恢复和任务重放。
- 活跃任务查询。
- 优雅取消正在运行的 LLM 或工具调用。
- 权限角色，不同合法客户端拥有相同 RPC 权限。
- 远程客户端访问。

### 安全边界

当前安全措施：

- Core 只允许绑定回环地址。
- CLI 启动时验证 host 必须是回环地址。
- daemon 使用随机 token。
- 每个 RPC 请求必须通过 token 鉴权。
- token 文件不会提交到 Git。
- 请求进入 Agent 前必须通过协议和参数验证。

当前安全限制：

- token 对本机当前用户可见，不是完整用户身份认证系统。
- 没有请求级权限模型。
- 没有 TLS。
- 没有 token 轮换和失效机制。
- daemon 生命周期管理不是操作系统服务级隔离。
- Agent 工具本身的安全性仍依赖工具层和容器沙盒。

### 模块职责边界

```text
cli/main.py
    只负责配置加载、命令注册、参数解析和统一错误处理。

cli/commands/
    负责各命令参数和用户工作流。

cli/client.py
    负责 JSON-RPC 请求与流式通知接收。

cli/daemon.py
    负责 Core 后台进程生命周期。

cli/render.py
    负责终端展示。

ipc/
    负责 CLI 与 Core 共享的协议模型和本地凭据。

core/bus/
    负责服务端分帧、路由、连接和响应。

core/agent/service.py
    负责一次 Agent turn 的业务编排。
```

以下依赖不允许出现：

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
- 不强制终止关闭超时的 daemon，只返回明确错误。
- Core 内部任务失败通过 RPC/流式事件返回，CLI 不尝试替代 Core 恢复业务状态。

## 后续方向

- 将项目包名从通用的 `src` 调整为正式包名，例如 `learn_agent`。
- 增加配置文件与环境变量覆盖机制。
- 为 TUI 复用 `ipc` 模型和 RPC client。
- Core 后续可继续拆分 `bus` 与 `transport`，并增加 `CoreApp` 组合根。
