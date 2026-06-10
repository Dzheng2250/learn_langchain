# Core Architecture

`src/core` 是后台业务执行端。它不读取终端输入，也不负责面向用户的输出。

## 模块边界

```text
main.py
  Core daemon 命令入口。

app.py
  组合 Router、handlers、Transport 和 Agent service，管理生命周期。

bus/
  context.py   Transport 无关的 handler 请求上下文
  router.py    JSON-RPC 验证、鉴权和方法分发

handlers/
  core.py      ping 和 shutdown handler
  agent.py     agent.chat 与流式 notification 适配

transport/
  framing.py       NDJSON 分帧与消息大小限制
  socket_server.py asyncio TCP 连接和写回

agent/
  graph.py     父 Agent LLM、LangGraph 节点和图
  service.py   一次完整 Agent turn 的业务编排和会话级锁

context/       有界短期上下文和摘要
memory/        PostgreSQL 会话、消息和长期记忆
tools/         工具、工具注册表和统一观测 ToolNode
hooks/         结构化事件、sink 和 helper
skills/        本地 Skill manifest 与正文加载
subagent/      非递归子 Agent
streaming/     LangGraph token/step/done 事件
database/      参数化 SQL 和 schema
```

## 依赖规则

1. `CoreApp` 是组合根，只负责依赖组装和生命周期。
2. `transport` 只负责连接和分帧，不得依赖 Agent、Memory 或 Tools。
3. `bus` 负责不可信请求进入 Core 前的验证和鉴权。
4. `handlers` 只适配 RPC 与业务服务，并依赖抽象 `RequestContext`。
5. `agent/service.py` 负责业务编排，不依赖 CLI、RPC 或 TCP。
6. `agent/graph.py` 只定义 Agent 图，不管理会话生命周期。
7. CLI 不得直接调用 memory、tools 或 Agent graph。
8. 同一 `session_id` 的 turn 串行执行，不同 session 可以并行。
9. 所有运行时 SQL 参数使用 psycopg 参数绑定。
10. 普通工具不手写通用开始/结束 hook，由 `ObservedToolNode` 集中记录。
11. CLI/Core 共享的协议模型和本地凭据位于顶层 `src/ipc/`。
12. 全局非敏感运行配置位于顶层 `src/config/`。

详细设计与审查结论见 [`docs/core-architecture.md`](../../docs/core-architecture.md)。
