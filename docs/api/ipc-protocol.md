# Core IPC 协议
本文定义 CLI、TUI、GUI 等客户端与 Core daemon 之间的稳定通信边界。

## 传输与分帧

| 项目 | 当前约定 |
|---|---|
| 传输 | 本机 TCP |
| 绑定地址 | loopback 地址，默认 `127.0.0.1` |
| 编码 | UTF-8 |
| 分帧 | NDJSON，每行一个完整 JSON 值 |
| 消息协议 | JSON-RPC 2.0 |
| 鉴权 | 每个 RPC 的 `params.auth_token` |
| 当前客户端模型 | 一次请求使用一条独立连接 |

一条请求必须以换行结束：

```json
{"jsonrpc":"2.0","id":"client-1","method":"core.ping","params":{"auth_token":"..."}}\n
```

Core 会拒绝无效 UTF-8、无效 JSON 和超过配置上限的 frame。无效 frame 只会关闭当前连接，不影响其他连接。

## 连接生命周期

普通请求：

```text
Client                      Core daemon
  |---- JSON-RPC request ------>|
  |<--- JSON-RPC response ------|
  |          close              |
```

Agent 请求：

```text
Client                      Core daemon
  |---- agent.chat request ---->|
  |<--- agent.event ----------- |  0..N 条通知
  |<--- final response -------- |
  |          close              |
```

通知与最终响应共享同一连接。客户端必须持续读取，直到收到与请求 `id` 相同的最终响应。

## 身份字段

| 字段 | 生成者 | 用途 |
|---|---|---|
| `id` / `request_id` | 客户端 | 关联一次 JSON-RPC 请求、通知与最终响应 |
| `run_id` | Core | 标识一次 `agent.chat` 或 `session.resume` |
| `execution_id` | Core | 标识可跨多次恢复的长期执行 |
| `session_name` | 客户端 | Workspace 内的人类可读 Session 名称 |
| `workspace_root` | 客户端 | 指定本次请求所属 Workspace |

客户端不得指定 `run_id`、`execution_id` 或 Trace ID。它们属于 Core 的执行与诊断身份，防止客户端伪造或碰撞。

## 鉴权与运行时发现

客户端从用户级 runtime 目录读取 daemon token，再放入每个请求的 `params.auth_token`。Token：

- 只用于本机 daemon 鉴权。
- daemon 重启后可能变化。
- 不得输出到日志、Trace 或界面。
- 不应由第三方客户端自行生成。

运行目录和端口由项目配置决定，详见[配置参考](/docs/reference/configuration-reference.md)。

## JSON-RPC 错误

Core 使用标准 JSON-RPC 错误响应：

```json
{
  "jsonrpc": "2.0",
  "id": "client-1",
  "error": {
    "code": -32602,
    "message": "Invalid params",
    "data": []
  }
}
```

错误码及恢复策略见[错误与恢复参考](/docs/api/error-reference.md)。

## 当前限制

- 不支持 TLS 或远程网络访问。
- 不支持 JSON-RPC batch。
- 不支持客户端 notification。
- 不支持同一连接并发多个请求。
- 不支持断线重连后继续订阅旧事件。
- 不提供协议版本协商；客户端应通过 `core.ping.server_version` 做基础兼容检查。
