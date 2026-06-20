# Agent 执行函数级调用链

> 文档状态：Current
> 权威范围：一次 Agent 请求从 CLI 到 Core、Graph、事件写回和最终响应的函数级调用顺序
> 维护触发：CLI 请求入口、Transport、Handler、Agent worker、Graph stream 或最终响应调用链变化

本文从函数和执行线程角度解释一次 `agent.chat` 如何穿过系统。高层组件职责、预算模型、工具注册和
扩展方式见 [Agent 执行架构](/docs/architecture/agent-execution-architecture.md)。

## 本文负责

- 记录一次请求经过的关键函数、参数和执行位置。
- 区分 CLI 主线程、Core asyncio loop 和 `agent-turn` worker。
- 说明事件如何从 worker 返回前端。
- 提供成功路径和失败路径的代码级排障入口。

## 本文不负责

- 不定义 Agent 的高层架构和扩展策略；这些属于 Agent 执行架构。
- 不定义数据库表、事务和 checkpoint 一致性；这些属于 State 文档。
- 不定义 RPC 字段和事件 payload；这些属于 `/docs/api/`。

## 代码级程序流转

本节按一次 `learn-agent chat --session default "读取项目结构"` 的真实调用顺序说明程序如何
流转。重点不是组件“有什么”，而是一个函数收到什么参数、调用哪个函数、在哪个执行环境运行，
以及产生什么副作用。

### 调用链总表

| 顺序 | 函数 | 执行位置 | 主要职责 |
|---:|---|---|---|
| 1 | `chat_once()` | CLI 主线程 | 识别 Workspace，构造 `agent.chat` 参数 |
| 2 | `CoreClient.request()` | CLI 主线程 | 建立 TCP 连接，发送 JSON-RPC，持续读取通知和最终响应 |
| 3 | `SocketServer._handle_connection()` | Core asyncio loop | 读取一条 NDJSON frame，并交给 Router |
| 4 | `RpcRouter.dispatch()` | Core asyncio loop | 验证协议、参数、方法和 token |
| 5 | `AgentHandlers.chat()` | Core asyncio loop | 创建 `run_id`，建立 worker 到 socket 的事件桥 |
| 6 | `AgentTurnService.run_turn()` | Core asyncio loop | 获取并发 slot，提交同步 Turn 到专用 executor |
| 7 | `AgentTurnService._run_turn_sync()` | `agent-turn` worker | 消费内部事件流，形成最终结果 |
| 8 | `AgentTurnService.stream_turn()` | `agent-turn` worker | 解析 Workspace/Session，获取 Session UUID 锁 |
| 9 | `AgentTurnService._stream_locked_turn()` | `agent-turn` worker | 加载上下文、执行 Graph、委托 TurnFinalizer |
| 10 | `TurnExecutionLoop.run()` | `agent-turn` worker | 控制 Slice 循环、预算和继续/停止判断 |
| 11 | `SliceExecutionService.run_slice()` | `agent-turn` worker | 调用 LangGraph，并生成稳定事件协议 |
| 12 | `stream_graph_events()` | `agent-turn` worker | 将 LangGraph stream 转换为稳定事件协议 |
| 13 | `agent_node()` / `ObservedToolNode` | `agent-turn` worker | 调用 LLM，或执行模型请求的工具 |
| 14 | `TurnLoopErrorHandler` / `TurnLoopPauseHandler` | `agent-turn` worker | 处理错误或暂停分支的状态更新和事件构造 |
| 15 | `TurnRunObserver` | `agent-turn` worker | 发送 run/slice 成功、暂停或失败的观测事件 |
| 16 | `on_event()` | `agent-turn` worker | 将流事件投递回 Core asyncio loop |
| 17 | `SocketRequestContext.send_notification()` | Core asyncio loop | 将 `agent.event` 通知写入 TCP |
| 18 | `CoreClient.request()` | CLI 主线程 | 匹配 `request_id`，渲染流事件，读取最终响应 |

### 第一阶段：CLI 构造请求

入口位于 `src/cli/commands/chat.py`：

```python
chat_once(client, session_name, message, workspace)
```

参数语义：

| 参数 | 来源 | 功能 |
|---|---|---|
| `client` | `CoreClient(config)` | 保存 Core 地址、连接超时和用户级 runtime 路径 |
| `session_name` | `--session`，默认 `default` | Workspace 内可读名称，不是数据库内部 UUID |
| `message` | CLI 位置参数或交互输入 | 当前用户请求 |
| `workspace` | `--workspace`，默认当前目录 | 显式 Workspace 起点，可为空 |

`chat_once()` 首先调用：

```python
workspace_root = discover_workspace_root(workspace)
```

`discover_workspace_root()` 从起点向父目录查找最近的 `.git`；找到后使用 Git 根目录，否则使用
起点目录。随后调用：

```python
client.request(
    "agent.chat",
    {
        "workspace_root": str(workspace_root),
        "session_name": session_name,
        "message": message,
    },
    on_event=render_agent_event,
)
```

`CoreClient.request()` 在 `src/cli/client.py` 中完成以下动作：

1. 生成只用于 JSON-RPC 关联的 `request_id`。
2. 从用户级 runtime 目录读取 daemon token。
3. 构造 JSON-RPC 2.0 请求，并把 token 放入 `params.auth_token`。
4. 使用 `socket.create_connection()` 建立单请求单连接 TCP。
5. 写入一行 UTF-8 JSON，换行符是 NDJSON frame 边界。
6. 循环读取服务端消息：
   - `method == "agent.event"`：交给 `on_event` 渲染。
   - `id == request_id`：这是最终成功或错误响应，结束请求。
   - 其他 `id`：忽略。

这里有两个不同 ID：

| ID | 创建位置 | 生命周期 | 用途 |
|---|---|---|---|
| `request_id` | `CoreClient.request()` | 一次 JSON-RPC 请求 | 匹配通知与最终响应 |
| `run_id` | `AgentHandlers.chat()` | 一次 Agent Turn | 串联事件、日志与执行结果 |

### 第二阶段：Transport、验证与路由

Core 的 TCP 入口是 `SocketServer._handle_connection()`：

```python
raw = await read_ndjson(reader, self.max_message_bytes)
response = await self.router.dispatch(raw, context)
await context.send_response(response)
```

这一层只处理连接和 frame，不理解 Agent。`SocketRequestContext` 提供：

- `request_id`：当前请求 ID，供通知关联。
- `send_notification()`：Handler 执行期间主动发送通知。
- `send_response()`：发送最终响应。
- `request_close()`：要求最终响应后关闭连接。

`RpcRouter.dispatch()` 按固定顺序执行：

```text
JsonRpcRequest.model_validate(raw)
  -> 查找 method 注册
  -> ChatParams.model_validate(request.params)
  -> verify_token()
  -> await AgentHandlers.chat(params, context)
  -> JsonRpcSuccess
```

`ChatParams` 是 Core 接受 `agent.chat` 的安全边界：

```python
workspace_root: str       # 1..4000 字符
session_name: str         # 1..200 字符，默认 default
message: str              # 1..200000 字符
auth_token: str           # 必填
```

在 Pydantic 参数验证和 token 验证成功前，Router 不会调用 Agent、工具或 shutdown handler。

### 第三阶段：Handler 建立异步与同步边界

`AgentHandlers.chat()` 位于 `src/core/handlers/agent.py`。它做两件事：

1. 创建 `run_id = uuid4().hex`。
2. 创建 `on_event(item)` 回调，把 worker 线程产生的事件送回 Core asyncio loop。

调用关系：

```python
return await self.agent_service.run_turn(
    params.workspace_root,
    params.session_name,
    params.message,
    on_event,
    run_id=run_id,
)
```

`AgentTurnService.run_turn()` 是异步外观，但当前 Agent 链路是同步的。它先获取
`asyncio.Semaphore`，再把 `_run_turn_sync()` 提交到专用 `ThreadPoolExecutor`：

```text
Core asyncio loop
  -> await semaphore.acquire()
  -> executor.submit(_run_turn_sync)
  -> await asyncio.wrap_future(worker_future)
```

参数功能：

| 参数 | 功能 |
|---|---|
| `workspace_root` | 确定本轮允许访问的文件、Skill、命令和记忆范围 |
| `session_name` | 在 Workspace 内解析或创建 Session UUID |
| `user_input` | 当前用户输入，进入上下文和记忆检索 |
| `on_event` | worker 线程调用的流事件回调 |
| `run_id` | 本轮观测身份；未传入时 Service 自行生成 |

信号量限制已提交和正在执行的 Turn 总数，避免默认 executor 形成无界排队。worker 完成后，
done callback 在 Core loop 中释放 slot。

### 第四阶段：解析身份并建立并发边界

worker 首先进入 `_run_turn_sync()`，它消费 `stream_turn()` 产生的事件：

```python
for item in self.stream_turn(...):
    on_event(item)
    # done 更新最终 result；error 更新错误结果
```

`stream_turn()` 的顺序是：

```text
去除输入首尾空白并拒绝空消息
  -> WorkspaceRepository.resolve(workspace_root)
  -> WorkspaceRepository.resolve_session(workspace, session_name)
  -> SessionLockRegistry.get(session.session_id)
  -> 检查模型配置
  -> 进入真实 Turn 或诊断路径
```

身份对象的职责：

| 对象 | 关键字段 | 作用 |
|---|---|---|
| `WorkspaceContext` | `workspace_id`, `root` | 确定隔离边界 |
| `SessionContext` | `session_id`, `session_name`, `workspace` | 确定会话归属 |
| `AgentRunContext` | `run_id`, `session`, `turn_index`, `limits` | 确定一次真实执行的身份和限制 |

Session 锁使用内部 `session_id`，不是 `session_name`。因此：

- 同一 Session 的“加载状态 → 执行 → 保存状态”严格串行。
- 不同 Workspace 都可拥有名为 `default` 的 Session，并可并行执行。
- 不同 Session 也可并行，但总并发受 `CORE_AGENT_WORKERS` 限制。

### 第五阶段：创建或复用 WorkspaceRuntime

模型配置有效时，`stream_turn()` 调用：

```python
runtime = self.runtime_registry.get(workspace)
```

`WorkspaceRuntimeRegistry.get()` 按 `workspace_id` 缓存 Runtime。缓存未命中时，
`WorkspaceRuntimeFactory.create()` 执行：

```text
create_workspace_toolset(workspace, model_provider)
  -> 创建绑定 workspace.root 的文件、Skill、总结和命令工具
  -> 注册 ToolSpec
  -> 生成 Sub-agent 工具视图
  -> 创建 delegate_to_subagent
  -> 生成 Parent Agent 工具视图
  -> freeze ToolRegistry

create_parent_graph(parent_tools, skill_manifest, model_provider)
  -> 创建绑定工具的 Parent LLM
  -> 注册 agent 节点和 tools 节点
  -> 编译 LangGraph
```

`WorkspaceRuntime` 在多轮之间复用，但其中的工具和 Graph 永久绑定到创建时的
`WorkspaceContext`。一次请求不能通过修改全局变量把 Runtime 切换到另一个目录。

### 第六阶段：准备真实 Turn 输入

`_stream_locked_turn()` 在 Session 锁内执行：

```python
state, turn_index = store.load_session(session)
current_turn = turn_index + 1
run_context = AgentRunContext(..., turn_index=current_turn, ...)
```

这里的持久化 `turn_index` 表示最后完成的 Turn；只有本轮成功完成后，Session 才保存为
`current_turn`。

随后加载长期记忆：

```python
store.retrieve_for_turn(
    workspace_id,
    user_input,
    new_session=turn_index == 0,
)
```

- `new_session=True`：合并 bootstrap memory 和相关记忆。
- `new_session=False`：只加载相关记忆。
- 查询始终携带 `workspace_id`。

`AgentContextManager.build_input_messages()` 按顺序构造 Graph 输入：

```text
可选的 Session summary SystemMessage
  -> 可选的长期记忆 SystemMessage
  -> 最近 RECENT_MESSAGE_LIMIT 条原始消息
  -> 当前 HumanMessage
```

这些输入中，summary 和长期记忆是合成上下文，只用于本轮模型输入，不应再次归档为用户历史。

### 第七阶段：LangGraph AgentLoop

`create_parent_graph()` 在 `src/core/agent/graph.py` 中定义循环：

```text
START
  -> agent_node
       -> LLM 无 tool_calls -> END
       -> LLM 有 tool_calls -> tools
  -> ObservedToolNode
       -> 追加 ToolMessage
  -> agent_node
```

`agent_node(state)` 接收 LangGraph `MessagesState`，把系统提示词放在最前面，然后调用：

```python
llm_with_tools.invoke(llm_messages)
```

返回值必须是：

```python
{"messages": [response]}
```

LangGraph 的 messages reducer 会把新响应追加到状态，而不是替换整个历史。

`ObservedToolNode` 继承 LangGraph `ToolNode`。模型输出的每个 `tool_call` 会按名称匹配
Workspace 工具，执行前后统一记录：

```text
tool_started
  -> execute(request)
  -> tool_finished 或 tool_failed
```

工具函数本身不需要手写通用调用边界 Hook。工具内部只记录自身特有的领域事件。

### 第八阶段：把 Graph stream 转换为稳定事件

`_stream_locked_turn()` 不直接消费 LangGraph 原始 stream，而是调用：

```python
stream_graph_events(graph, input_messages, run_context)
```

适配器使用：

```python
app.stream(
    inputs,
    config={"recursion_limit": limits.max_graph_steps},
    stream_mode=["messages", "values"],
)
```

两种 LangGraph stream mode 的用途：

| mode | 内容 | 转换结果 |
|---|---|---|
| `messages` | LLM 增量 `AIMessageChunk` | `token` |
| `values` | 当前完整 Graph 状态快照 | `step`、工具计数和最终消息 |

适配器对外只产生四种稳定事件：

| 事件 | 含义 |
|---|---|
| `token` | 可立即渲染的模型文本增量 |
| `step` | Agent 开始、工具请求、工具结果或完整 Agent 消息 |
| `error` | Graph、步骤限制或工具调用限制导致本轮失败 |
| `done` | Graph 正常结束，携带完整最终 messages |

`max_graph_steps` 通过 LangGraph `recursion_limit` 实施；`max_tool_calls` 由适配器累计
新消息中的 `tool_calls` 实施。达到限制时返回结构化 `error`，不继续持久化失败 Turn。

### 第九阶段：流事件返回 CLI

worker 每产生一个事件，`_run_turn_sync()` 就调用 `on_event(item)`。该回调仍运行在 worker
线程，不能直接操作 asyncio socket，因此 `AgentHandlers.chat()` 使用：

```python
asyncio.run_coroutine_threadsafe(
    context.send_notification(notification),
    loop,
)
```

通知格式：

```json
{
  "method": "agent.event",
  "params": {
    "request_id": "用于匹配当前 CLI 请求",
    "run_id": "用于追踪当前 Agent Turn",
    "event": "token | step | error | done",
    "data": {}
  }
}
```

若客户端断线，第一次通知失败会记录 `stream_notification_failed`，后续停止发送通知，但 worker
继续尝试完成执行和持久化。客户端退出不会主动中断 Core Turn，但其他执行或数据库异常仍可能
导致本轮失败。

### 第十阶段：成功完成后的持久化

只有收到 LangGraph `done` 时，`TurnCoordinator` 才调用 `TurnFinalizer`：

```python
finalization = turn_coordinator.finalize(
    store=store,
    session=session,
    turn_index=current_turn,
    previous_state=state,
    final_messages=item["data"]["messages"],
    execution=execution,
)
```

最小提交和派生维护边界：

| 顺序 | 调用 | 数据库结果 |
|---:|---|---|
| 1 | `build_fast_state()` | 纯计算近期消息，不调用摘要模型 |
| 2 | `CompletedTurnCommitter.commit()` | 同事务写入消息、Session、Execution 和维护任务 |
| 3 | 返回最终 `done` | CLI 可以恢复输入 |
| 4 | `MaintenanceScheduler` | 后台执行摘要、记忆和 checkpoint 清理 |

长期记忆 handler 只读取已提交消息，因此来源关系始终引用有效消息。用户明确要求“记住”时也
返回 `pending`；CLI 不会把尚未完成的后台提取表述为已经保存。

### 第十一阶段：最终响应与资源恢复

成功时 `_stream_locked_turn()` 产生最终 `done`：

```python
{
    "run_id": run_id,
    "status": "ok",
    "workspace_id": "...",
    "session_id": "...",
    "session_name": "...",
    "stop_reason": "completed",
    "tool_call_count": 3,
    "durability": "committed",
    "maintenance_status": "pending",
    "memory_status": "pending",
}
```

`_run_turn_sync()` 将其转换为 Handler 的最终 result。`RpcRouter.dispatch()` 包装为
`JsonRpcSuccess`，`SocketServer` 写回连接，CLI 收到与 `request_id` 匹配的响应后结束读取。

无论成功或失败，`_stream_locked_turn()` 的 `finally` 都会：

```text
恢复进入本轮前的事件上下文
  -> 关闭本轮 StateStore facade
  -> 释放 Session UUID 锁
  -> worker future 完成
  -> 释放并发 semaphore slot
```

### 失败路径速查

| 失败位置 | 转换方式 | 是否继续业务执行 |
|---|---|---|
| NDJSON/JSON 无效 | Parse Error，关闭当前连接 | 否 |
| JSON-RPC 或参数无效 | 标准 JSON-RPC error | 否 |
| token 错误 | Unauthorized | 否 |
| LLM 未配置 | 进入无状态 diagnostic path | 不执行 Graph |
| Graph recursion limit | `error(graph_step_limit)` | 不持久化失败 Turn |
| 工具调用数超限 | `error(tool_call_limit)` | 不持久化失败 Turn |
| LLM、Graph 或 Turn 异常 | `error` + 观测事件 | 不持久化失败 Turn |
| CLI 流通知断线 | 停止通知并记录事件 | Core Turn 继续 |
| 后台记忆提取失败 | `memory_failed` 事件 | 已完成 Turn 不回滚 |

### 完整数据流动示意图

完整数据流按阅读层次拆为三张图。先理解进程间边界，再下钻 Core 内部 Turn，最后查看
Agent 循环。每张图只描述一个层次，避免将所有实现细节堆叠在同一画布中。

#### 1. 端到端进程总览

这张图回答：用户输入如何进入 daemon，执行结果如何回到 CLI。

```mermaid
flowchart LR
    subgraph CLI["CLI 进程"]
        direction TB
        User["用户"]
        Chat["chat command<br/>识别 Workspace"]
        Client["CoreClient"]
        Render["终端渲染"]
        User --> Chat --> Client
        Render --> User
    end

    subgraph IPC["IPC / socket"]
        direction TB
        Request["agent.chat<br/>JSON-RPC 请求"]
        Notify["agent.event<br/>流式通知"]
        Result["最终响应"]
    end

    subgraph Core["Core daemon"]
        direction TB
        Boundary["SocketServer + RpcRouter<br/>解析、验证、鉴权"]
        Handler["AgentHandlers<br/>run_id + 协议适配"]
        Turn["AgentTurnService<br/>执行完整 Turn"]
        Boundary --> Handler --> Turn
    end

    Client --> Request --> Boundary
    Turn --> Notify --> Render
    Turn --> Result --> Client

    classDef entry fill:#fff3cd,stroke:#d6a100,color:#222;
    classDef ipc fill:#f3e8ff,stroke:#8b5fbf,color:#222;
    classDef core fill:#dce9ff,stroke:#5b85c5,color:#222;
    class User,Chat,Client,Render entry;
    class Request,Notify,Result ipc;
    class Boundary,Handler,Turn core;
```

#### 2. Core 内单轮 Turn

这张图回答：一次经过验证的请求，在 Core 中如何完成准备、执行和持久化。

```mermaid
flowchart LR
    subgraph Prepare["阶段一：准备"]
        direction TB
        Resolve["解析 Workspace<br/>与 Session"]
        Lock["获取 Session UUID 锁"]
        Load["加载短期上下文<br/>与 Workspace 记忆"]
        Build["构造 AgentRunContext<br/>与模型输入"]
        Resolve --> Lock --> Load --> Build
    end

    subgraph Execute["阶段二：执行"]
        direction TB
        Slot["获取并发 slot"]
        Worker["agent-turn worker"]
        Graph["执行 WorkspaceRuntime.graph"]
        Slot --> Worker --> Graph
    end

    subgraph Persist["阶段三：最小提交与后台维护"]
        direction TB
        Commit["CompletedTurnCommitter<br/>原子提交业务事实"]
        State["state.db<br/>消息 / Session / Execution"]
        Jobs["maintenance_jobs<br/>持久化任务"]
        Done["返回 done<br/>CLI 恢复输入"]
        Maintenance["MaintenanceScheduler<br/>摘要 / 记忆 / checkpoint 清理"]
        Commit --> State --> Done
        Commit --> Jobs --> Maintenance
    end

    Build --> Slot
    Graph --> Commit
    Resolve <--> State
    Load <--> State

    classDef prepare fill:#fff3cd,stroke:#d6a100,color:#222;
    classDef execute fill:#dce9ff,stroke:#5b85c5,color:#222;
    classDef persist fill:#dff2df,stroke:#629b62,color:#222;
    classDef store fill:#f3e8ff,stroke:#8b5fbf,color:#222;
    class Resolve,Lock,Load,Build prepare;
    class Slot,Worker,Graph execute;
    class Commit,Done,Maintenance persist;
    class State,Jobs store;
```

#### 3. Parent Agent 与 Sub-agent 调用链

这张图回答：LangGraph 如何循环调用 LLM、工具和非递归 Sub-agent。

```mermaid
flowchart LR
    subgraph ParentLoop["Parent Agent loop"]
        direction TB
        Start["模型输入"]
        Parent["Parent Agent LLM"]
        Decision{"是否调用工具"}
        ToolNode["ObservedToolNode"]
        Tools["Workspace 工具"]
        Done["完成响应"]

        Start --> Parent --> Decision
        Decision -->|"否"| Done
        Decision -->|"是"| ToolNode
        ToolNode --> Tools
        Tools -->|"ToolMessage"| Parent
    end

    subgraph SubagentLoop["Sub-agent loop"]
        direction TB
        Delegate["delegate_to_subagent"]
        Subagent["非递归 Sub-agent"]
        SubTools["Sub-agent 工具视图<br/>不包含委派工具"]
        Delegate --> Subagent
        Subagent --> SubTools --> Subagent
    end

    ToolNode --> Delegate
    Subagent -->|"任务总结"| Parent

    classDef input fill:#fff3cd,stroke:#d6a100,color:#222;
    classDef agent fill:#dce9ff,stroke:#5b85c5,color:#222;
    classDef tool fill:#dff2df,stroke:#629b62,color:#222;
    classDef decision fill:#f3e8ff,stroke:#8b5fbf,color:#222;
    class Start,Done input;
    class Parent,Subagent agent;
    class ToolNode,Tools,Delegate,SubTools tool;
    class Decision decision;
```

三张图共同表达的关键边界：

- `AgentHandlers` 和 `SocketServer` 运行在 Core asyncio 事件循环中。
- `WorkspaceRepository`、Memory、LangGraph、LLM 和工具执行位于专用 `agent-turn` worker。
- worker 通过 `on_event` 回调和 `asyncio.run_coroutine_threadsafe()` 将流式通知送回事件循环。
- 完整消息和最小 Session 状态原子写入 `state.db`；摘要、长期记忆和 checkpoint 清理通过持久化维护任务完成。
- 客户端断线后，流式通知停止；已经进入 worker 的 turn 仍继续执行并完成持久化。

