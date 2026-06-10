# CLI / Core 双进程与 JSON-RPC 设计说明

## 1. 为什么要从单进程改为双进程

当前项目的 CLI 输入、Agent 执行、上下文管理、记忆读写和工具调用都运行在同一个 Python 进程中：

```text
用户输入 -> CLI 循环 -> Agent -> Tool / Memory -> CLI 输出
```

这种结构适合学习和快速验证，但随着功能增加会遇到几个问题：

1. CLI 关闭后，所有只能存在于进程内的状态和后台任务都会停止。
2. CLI、未来的 TUI、移动端或其他客户端都需要重复集成 Agent 执行逻辑。
3. Agent 执行时间较长时，前台界面容易被阻塞。
4. 前台界面与工具执行、数据库连接、后台任务耦合，难以独立维护。
5. 无法让多个客户端共享同一个 Agent 服务和会话数据。

双进程结构将系统拆成：

```text
前台进程：CLI / TUI
    负责交互、展示和发送请求

后台进程：Core daemon
    负责 Agent、工具、上下文、记忆和数据库
```

前台不再直接调用 Agent，而是通过进程间通信请求 Core 执行任务。

这种结构的核心思想是：

> UI 只是客户端，Core 才是唯一可信的业务执行端。

## 2. 什么是 daemon

daemon 是长期运行的后台进程。它不依赖某一个 CLI 会话，也不直接读取用户输入。

在本项目中，Core daemon 启动后会：

1. 初始化数据库和 Agent 服务。
2. 监听本机 TCP 端口，例如 `127.0.0.1:18765`。
3. 等待 CLI 发来 JSON-RPC 请求。
4. 验证请求并调用对应业务方法。
5. 将流式事件和最终结果返回 CLI。
6. 持续运行，直到收到合法的关闭请求。

因此，关闭一个 CLI 窗口不等于关闭 Core。后续 TUI 也可以连接同一个 Core。

## 3. 为什么选择本地 TCP + NDJSON

进程间通信需要一种传输通道。常见方案包括：

- TCP
- Unix Domain Socket
- Windows Named Pipe
- 标准输入输出管道
- Redis 或消息队列
- HTTP / WebSocket

本项目 v1 选择本地 TCP，原因是：

1. Python 标准库 `asyncio` 原生支持，跨 Windows、Linux 和 macOS。
2. CLI、TUI 和未来其他客户端都容易接入。
3. 支持一个 daemon 同时接受多个连接。
4. 后续可以平滑升级为远程服务，但 v1 仍只监听本机。

TCP 只传输连续字节流，本身没有“消息边界”。如果连续发送两段 JSON：

```text
{"id":1}{"id":2}
```

接收端无法直接判断第一条消息在哪里结束。

因此使用 NDJSON，即每行一个完整 JSON 对象：

```text
{"jsonrpc":"2.0","id":"1","method":"core.ping","params":{...}}\n
{"jsonrpc":"2.0","id":"2","method":"agent.chat","params":{...}}\n
```

接收端按行读取，每读取一行就获得一条完整消息。NDJSON 是传输分帧方式，JSON-RPC 是消息内容协议，两者职责不同。

## 4. 什么是 JSON-RPC

JSON-RPC 是一种使用 JSON 表达远程方法调用的协议。

CLI 希望 Core 执行某个操作时，不是直接调用 Python 函数，而是发送一条请求：

```json
{
  "jsonrpc": "2.0",
  "id": "cli-1",
  "method": "core.ping",
  "params": {
    "auth_token": "..."
  }
}
```

字段含义：

| 字段 | 作用 |
| --- | --- |
| `jsonrpc` | 协议版本，固定为 `"2.0"` |
| `id` | 请求标识，用于将响应与请求对应起来 |
| `method` | 希望调用的远程方法 |
| `params` | 方法参数 |

Core 成功执行后返回：

```json
{
  "jsonrpc": "2.0",
  "id": "cli-1",
  "result": {
    "status": "ok"
  }
}
```

执行失败时返回：

```json
{
  "jsonrpc": "2.0",
  "id": "cli-1",
  "error": {
    "code": -32602,
    "message": "Invalid params"
  }
}
```

`id` 非常重要。因为同一个连接中可能同时存在多个请求，CLI 必须依靠 `id` 判断某条响应属于哪个请求。

## 5. 为什么 Core 必须验证请求

需要验证，而且验证必须发生在 Core 中。

CLI 只是一个客户端。即使项目自带的 CLI 会正确构造请求，本机其他程序仍然可以直接连接 daemon 端口并发送任意数据。

如果 Core 信任 CLI，攻击程序可能绕过 CLI，直接请求 Agent 执行工具、读取文件或关闭 daemon。

正确的信任边界是：

```text
不可信区域                         可信业务区域

CLI / TUI / 其他本机进程
        |
        v
TCP -> 解码 -> JSON 解析 -> 协议验证 -> 鉴权 -> 参数验证
                                              |
                                              v
                                      Agent / Tool / Memory
```

只有通过全部验证后，请求才能进入业务层。

Core 至少需要验证：

1. 消息长度没有超过限制。
2. 字节可以正确解码为 UTF-8。
3. 内容是合法 JSON。
4. 内容符合 JSON-RPC 2.0 结构。
5. `method` 在允许调用的方法表中。
6. `params` 符合该方法的数据模型。
7. `auth_token` 正确。
8. `session_id`、消息文本等业务参数合法。

Pydantic 负责结构和参数验证。验证失败时，Bus 返回标准 JSON-RPC 错误，不调用业务代码。

## 6. 为什么验证和通信放在 `core/bus`

`core/bus` 是 Core 的通信边界层。它负责“请求如何安全到达业务服务”，但不实现 Agent 业务。

建议职责如下：

```text
src/core/bus/
  models.py    JSON-RPC 请求、响应、错误和参数模型
  framing.py   NDJSON 读取、编码、消息长度限制
  auth.py      token 创建、读取和验证
  router.py    method 到 handler 的注册与分发
  server.py    TCP 服务、连接管理和流式写回
```

数据流如下：

```text
TCP 字节
  -> framing：读取一行并限制大小
  -> models：解析 JSON 并验证结构
  -> auth：验证本地 token
  -> router：找到 method 对应的 handler
  -> handler：调用 AgentTurnService
  -> server：写回通知或最终响应
```

这样拆分有三个好处：

1. Agent 服务不需要知道请求来自 CLI、TUI 还是测试程序。
2. 协议错误、鉴权和网络异常不会散落在业务代码中。
3. 未来替换 TCP 或增加 HTTP 接口时，不需要重写 Agent。

`core/bus` 不应直接包含 LangGraph 节点、记忆提取策略或工具实现。

## 7. 为什么 Agent 需要独立服务层

当前 `src/core/agent/runtime.py` 同时承担：

- `input()` 读取用户输入
- `print()` 展示结果
- 加载会话
- 执行 Agent
- 保存上下文
- 提取长期记忆

双进程改造后，这些职责必须拆开。

建议增加 `AgentTurnService`，它只处理一次 Agent turn：

```text
输入：
  session_id
  user_message

输出：
  token / step / error / done 事件流
  最终执行状态
```

一次 turn 的内部流程：

```text
加载 session 上下文
  -> 检索长期记忆
  -> 构造 LLM 输入
  -> 执行 LangGraph
  -> 产生 token 和 step 事件
  -> 归档完整消息
  -> 更新并保存短期上下文
  -> 按策略提取长期记忆
  -> 返回完成状态
```

服务层不能调用 `input()`，也不能直接打印面向用户的内容。CLI 如何展示 token，未来 TUI 如何绘制步骤，都不属于 Agent 服务的职责。

## 8. 流式输出为什么需要 JSON-RPC notification

普通 JSON-RPC 请求通常只有一个最终响应：

```text
请求 -> 等待 -> 最终响应
```

但 Agent 一轮执行可能持续很久，中间会产生：

- LLM token
- 工具调用开始
- 工具调用结果
- Agent 步骤
- 错误

如果只返回最终响应，CLI 在几十秒内不会显示任何内容，用户也无法知道 Agent 正在做什么。

因此本项目采用：

```text
一个 agent.chat 请求
  -> 多条 agent.event 通知
  -> 一条最终 JSON-RPC 响应
```

通知示例：

```json
{
  "jsonrpc": "2.0",
  "method": "agent.event",
  "params": {
    "request_id": "cli-1",
    "run_id": "run-123",
    "event": "token",
    "data": {
      "content": "正在"
    }
  }
}
```

最终响应示例：

```json
{
  "jsonrpc": "2.0",
  "id": "cli-1",
  "result": {
    "run_id": "run-123",
    "status": "ok"
  }
}
```

这里同时存在两个标识：

- `request_id`：关联 CLI 发出的请求。
- `run_id`：关联 Core 内部的一次 Agent 执行及 hook 事件。

一个请求可以产生很多通知，但只有一个最终响应。

## 9. 一次 `agent.chat` 的完整数据流

```text
CLI 进程                                               Core daemon 进程

读取用户输入
  |
构造 agent.chat 请求
  |
附加 auth_token
  |
写入一行 NDJSON -------------------------------------> TCP 接收
                                                       |
                                                       校验消息长度和 UTF-8
                                                       |
                                                       json.loads
                                                       |
                                                       Pydantic 验证 JSON-RPC
                                                       |
                                                       验证 auth_token
                                                       |
                                                       router 分发 agent.chat
                                                       |
                                                       获取 session_id 对应锁
                                                       |
                                                       AgentTurnService 执行
                                                       |
显示 token <----------------------------------------- agent.event: token
显示工具步骤 <--------------------------------------- agent.event: step
                                                       |
                                                       保存上下文和记忆
                                                       |
显示完成状态 <--------------------------------------- JSON-RPC result
```

Core 是实际执行者。CLI 不持有 LangGraph、数据库连接、工具对象或会话上下文。

## 10. 为什么需要会话级锁

假设同一个 session 同时收到两个请求：

```text
请求 A：读取 turn_index=10
请求 B：读取 turn_index=10

请求 A 保存 turn_index=11
请求 B 也保存 turn_index=11
```

请求 B 可能覆盖请求 A 的上下文，造成消息丢失。

因此，同一个 `session_id` 必须串行执行：

```text
session-a: 请求 1 -> 请求 2 -> 请求 3
session-b: 请求 1 -> 请求 2
```

不同 session 之间可以并行：

```text
时间轴：
session-a 请求 1  =================>
session-b 请求 1      =================>
```

这种模型兼顾数据正确性和并发能力。

锁必须覆盖该 turn 的“加载上下文 -> 执行 -> 保存上下文”完整过程。只锁数据库保存阶段无法防止读取旧状态。

## 11. daemon 生命周期如何管理

v1 使用显式命令管理 daemon：

```text
learn-agent start
learn-agent status
learn-agent stop
learn-agent chat
```

运行时目录：

```text
.agent_runtime/
  daemon.pid
  daemon.token
  daemon.log
```

文件作用：

| 文件 | 作用 |
| --- | --- |
| `daemon.pid` | 记录 daemon 进程 ID，辅助状态判断 |
| `daemon.token` | 保存随机鉴权 token |
| `daemon.log` | 保存后台进程日志 |

启动流程：

```text
CLI start
  -> 检查 daemon 是否已运行
  -> 创建运行目录和随机 token
  -> 启动 learn-agent-core serve 后台进程
  -> 等待 core.ping 成功
  -> 返回启动成功
```

停止流程：

```text
CLI stop
  -> 读取 token
  -> 调用 core.shutdown
  -> daemon 停止接收新请求
  -> 等待活跃请求在超时时间内结束
  -> 关闭资源并退出
```

不能只依赖 PID 文件判断 daemon 存活，因为 PID 文件可能残留，操作系统也可能复用 PID。可靠状态检查应以合法的 `core.ping` 响应为准。

## 12. 本地接口为什么仍然需要鉴权

监听 `127.0.0.1` 只能阻止其他机器访问，不能阻止本机其他进程访问。

本项目的 Agent 可以调用工具、读取工作区，甚至运行受限命令。因此本机恶意程序不应该能够直接调用 Core。

v1 使用随机 token：

1. daemon 启动时安全生成 token。
2. token 保存到 `.agent_runtime/daemon.token`。
3. CLI 读取 token，并附加到每个请求中。
4. Core 使用恒定时间比较验证 token。
5. token 错误时，Core 在进入业务层前拒绝请求。

`.agent_runtime` 必须加入 `.gitignore`。token 不能放入普通项目配置或提交到 Git。

这种方案提供基础本地保护，但不是远程服务级安全方案。未来若允许非本机连接，需要 TLS、用户身份、权限模型和更完整的审计。

## 13. Core 和 CLI 入口为什么都要修改

原入口：

```text
agent_loop.py
  -> run_agent_loop()
  -> input()
  -> 直接执行 Agent
```

改造后需要两个独立入口：

```text
learn-agent
  -> src.cli.main:main
  -> start / stop / status / chat

learn-agent-core
  -> src.core.main:main
  -> serve
```

Core 入口负责启动服务，不再负责终端交互：

```text
src/core/main.py
  -> 初始化 Core
  -> 启动 TCP JSON-RPC server
  -> 等待 shutdown
```

CLI 入口负责用户交互，不再直接导入和执行 Agent：

```text
src/cli/main.py
  -> 解析命令
  -> 连接 Core
  -> 发送 JSON-RPC
  -> 渲染流式事件
```

根目录 `agent_loop.py` 可以暂时保留为兼容入口，但它应该转发到新 CLI，而不是继续执行旧的单进程循环。

## 14. 建议的模块边界

```text
src/
  cli/
    main.py       CLI 命令入口
    client.py     JSON-RPC 客户端
    daemon.py     daemon 启停和状态管理
    render.py     终端流式展示

  core/
    main.py       daemon 入口

    bus/
      models.py   JSON-RPC 数据模型
      framing.py  NDJSON 分帧
      auth.py     本地 token
      router.py   RPC 方法路由
      server.py   asyncio TCP 服务

    agent/
      graph.py    LangGraph 定义
      service.py  一次 Agent turn 的业务编排
```

依赖方向应保持单向：

```text
CLI -> JSON-RPC 协议
Core Bus -> Agent Service
Agent Service -> Agent / Context / Memory / Tools
```

不应出现：

```text
Agent Service -> CLI
Tool -> TCP Server
CLI -> PostgresMemoryStore
```

## 15. 错误如何分层处理

不同错误应该由不同层负责：

| 错误 | 负责层 | 处理方式 |
| --- | --- | --- |
| JSON 无法解析 | Bus framing/server | 返回 parse error |
| JSON-RPC 结构错误 | Bus models | 返回 invalid request |
| 参数类型或内容错误 | Bus models/router | 返回 invalid params |
| token 错误 | Bus auth | 拒绝请求 |
| method 不存在 | Bus router | 返回 method not found |
| Agent 或工具异常 | Agent Service | 产生 error 事件并返回失败结果 |
| CLI 无法连接 | CLI client | 提示 daemon 未运行或连接失败 |
| CLI 中途退出 | Core server | 停止写回，但已开始任务继续执行 |

Bus 不能因为一个坏请求崩溃。Agent 的业务异常也不应该关闭整个 daemon。

## 16. 当前设计的限制

v1 明确不处理以下问题：

1. 不支持取消正在运行的 Agent turn。
2. 不支持远程网络访问。
3. 不支持同一 session 并行写入。
4. 不提供会话列表和历史记录 RPC。
5. 客户端断开后，任务仍会在后台完成，但客户端无法重新订阅该次流。
6. daemon 重启后，不恢复未完成任务。

这些限制是有意的。v1 的目标是先建立稳定、可验证的 CLI/Core 通信边界，而不是一次完成完整任务系统。

## 17. 后续可演进方向

完成 v1 后，可以按需求继续扩展：

1. 增加 `session.list`、`session.history` 和 `session.create`。
2. 增加任务注册表，以及 `task.status`、`task.cancel`、事件重放。
3. 将长任务与 TCP 连接解耦，支持客户端断线重连。
4. 为 TUI 提供结构化工具步骤和状态事件。
5. 增加权限策略，限制不同客户端可调用的方法和工具。
6. 增加协议版本协商，避免 CLI 与 daemon 版本不兼容。
7. 如果需要跨机器访问，再升级为 TLS、用户鉴权和远程 API。

## 18. 核心决策总结

| 决策 | 原因 |
| --- | --- |
| Agent 放在 daemon 中执行 | 保持唯一业务执行端，支持多个前端 |
| CLI 只负责交互 | 避免 UI 与 Agent、数据库和工具耦合 |
| 使用本地 TCP | 跨平台、标准库支持、方便未来客户端接入 |
| 使用 NDJSON | 为 TCP 字节流提供简单可靠的消息边界 |
| 使用 JSON-RPC | 统一请求、响应、错误和方法分发格式 |
| Core 验证所有请求 | CLI 和本机其他进程都不能被默认信任 |
| 通信边界放入 `core/bus` | 将协议、安全和网络逻辑与业务隔离 |
| 使用流式 notification | 保留现有 Agent token 和步骤级输出 |
| 同 session 串行 | 防止上下文和 turn_index 并发覆盖 |
| 不同 session 并行 | 在保证正确性的同时提供并发能力 |
| 使用随机本地 token | 防止本机其他进程直接调用 Core |
| Core 和 CLI 使用独立入口 | 两者是不同职责、不同生命周期的进程 |

## 19. 当前实现对应关系

本文设计已经在项目中落地：

| 设计职责 | 当前实现 |
| --- | --- |
| CLI 命令入口 | `src/cli/main.py` |
| JSON-RPC 客户端 | `src/cli/client.py` |
| daemon 启停与状态 | `src/cli/daemon.py` |
| Core daemon 入口 | `src/core/main.py` |
| JSON-RPC 模型与参数验证 | `src/ipc/models.py` |
| NDJSON 分帧 | `src/core/bus/framing.py` |
| token 鉴权 | `src/ipc/auth.py` |
| 方法注册与分发 | `src/core/bus/router.py` |
| TCP server 与流式通知 | `src/core/bus/server.py` |
| 单次 Agent turn 编排 | `src/core/agent/service.py` |

当前 RPC 方法包括：

```text
core.ping
core.shutdown
agent.chat
agent.event
```

默认监听地址为 `127.0.0.1:18765`。运行时 PID、token 和日志位于 `.agent_runtime/`，该目录不会提交到 Git。
