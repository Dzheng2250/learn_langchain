# TUI 与其他前端接入指南
本文面向希望替换当前 CLI、实现 TUI/GUI 的开发者。

## 前端职责

前端负责：

- 发现 Core 地址和读取 daemon token。
- 构造并验证 JSON-RPC 请求。
- 持续读取 `agent.event`。
- 渲染 token、步骤、暂停和错误。
- 收到最终响应后结束当前请求。

前端不负责：

- 直接调用 Agent、Tool、Memory 或数据库。
- 创建 `run_id`、`execution_id` 或 Trace ID。
- 在断线后猜测执行状态。
- 将 Core 内部类作为公共接口使用。

依赖方向必须保持：

```text
TUI -> src.ipc <- Core
```

## 为什么不能直接复用当前 `CoreClient`

当前 `src/cli/client.py` 是同步、单请求单连接客户端，适合命令行，但会阻塞 TUI 的事件循环。

TUI 应实现异步客户端：

```python
class AsyncCoreClient:
    async def request(self, method, params):
        ...

    async def stream_request(self, method, params):
        ...
```

推荐使用 `asyncio.open_connection()`，并复用：

- `src.ipc.models` 中的 JSON-RPC 模型。
- `src.ipc.auth.read_token()`。
- Core 的 NDJSON 协议约定。

不要导入 `src.core.*`。

## 最小异步调用流程

```python
reader, writer = await asyncio.open_connection(host, port)
writer.write((json.dumps(request) + "\n").encode("utf-8"))
await writer.drain()

while True:
    raw = json.loads((await reader.readline()).decode("utf-8"))
    if raw.get("method") == "agent.event":
        render_event(raw["params"])
        continue
    if raw.get("id") == request_id:
        handle_final_response(raw)
        break
```

生产实现还必须处理：

- frame 大小限制。
- EOF、无效 UTF-8 和无效 JSON。
- Pydantic 响应校验。
- 写入锁，避免并发 frame 交叉。
- 窗口退出时的连接关闭。

## 推荐前端状态模型

```text
Idle
  -> Connecting
  -> Running
       -> Streaming
       -> Paused
       -> Failed
       -> Completed
```

界面至少应保存：

```text
request_id
run_id
workspace_root
session_name
received_token
connection_state
last_stop_reason
```

`execution_id` 由 `done` 或 `session.status` 提供，用于展示恢复能力。

## 渲染建议

- `token`：增量追加到当前回答。
- `step.agent_message`：仅在没有 token 时作为完整回答兜底。
- `step.tool_call_start/result`：显示为可折叠进度，不默认展示完整参数/结果。
- `done.status=paused`：提供 Resume/Discard 操作。
- `error`：展示安全消息，并根据 `retryable` 与 `error_action`给出操作。

## 断线处理

TUI 关闭或连接中断时，不应自动重发 `agent.chat`。重新连接后：

1. 调用 `session.status`。
2. 若 `execution_recoverable=true`，展示恢复操作。
3. 用户确认后调用 `session.resume`。

## 当前能力边界

当前协议没有：

- 执行中取消。
- 断线后事件续传。
- Session 列表和历史查询。
- 多连接订阅同一个 Run。
- capabilities 协商。

实现 TUI 时应将这些能力视为未来扩展，不要通过读取 SQLite 或 Core 私有模块绕过协议。
