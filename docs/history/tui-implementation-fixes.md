# TUI 实现问题与修复记录

> 文档状态：Historical
> 权威范围：TUI 流式渲染、认证和 Schema 迁移问题的历史修复记录
> 维护触发：仅在补充相关历史背景时更新

本文记录 TUI 开发期间遇到的问题和当时的修复方案，不代表当前架构契约。
当前 TUI 组件职责见 [TUI 架构](/docs/architecture/tui-architecture.md)。

## 3. 关键问题与解决

### 3.1 流式 token 换行问题

**症状**：每个 token chunk 在 RichLog 中变成独立的一行，导致 AI 回复被拆成几十行。

**原因**：`RichLog.write()` 每次调用都会在 `self.lines` 中创建一条新的视觉条目。如果每个 token chunk 都调用一次 `write()`，每块都独占一行。

**第一次修复（全量重绘）**：

```python
def _render_token_buffer(self) -> None:
    self._redraw(include_token=True)

def _redraw(self, *, include_token=False) -> None:
    self.clear()
    for entry in self._entries:
        self.write(entry)
    if include_token and self._token_buf:
        self.write(self._token_buf)
    self.refresh()
```

每次 token 到达都 **清空整屏 → 重写所有已提交条目 → 重写当前 token 缓冲**。正确解决了换行问题，但复杂度 O(N)。

**第二次修复（原地替换）**（推荐，但用户选择了全量重绘方案）：

```python
def _render_token_buffer(self) -> None:
    if self._token_line_start is None:
        self._token_line_start = len(self.lines)
    else:
        del self.lines[self._token_line_start:]
    self.write(self._token_buf)
    self.refresh()
```

只删除上次 token 渲染的视觉行，再写入新的。复杂度 O(1)。缺点是需要谨慎处理 RichLog 的内部行拆分。

**结论**：当前使用全量重绘方案（复杂度 O(N)，N=已提交条目数）。在典型会话中 N < 100，Textual 的虚拟终端操作足够快，不会造成卡顿。

### 3.2 Context Token 用量展示

**需求**：状态栏显示 `ctx: 3K/128K (2%)`，反映当前 session 的上下文 token 消耗。

**数据链路**（跨 Core + TUI 多个层次）：

```
LLM Response
  → _TokenTrackerCallback (src/core/llm/provider.py)
    记录 input_tokens, output_tokens
  → ExecutionBudget.input_tokens / output_tokens
  → TracingModelProvider 代理调用
  → AgentTurnService._stream_locked_turn
    在 done 事件中携带 context_tokens snapshot
  → TurnFinalizer.finalize()
    build_fast_state() 后更新 context_tokens
  → SQLiteSessionStore.save_fast_context()
    持久化 context_tokens 到 state.db sessions 表
  → AsyncCoreClient.on_event 回调
  → ChatScreen._handle_done()
    解析 context_tokens → StatusBar.set_usage()
```

**涉及的修改**：

| 文件 | 改动 |
|---|---|
| `src/core/llm/provider.py` | 新增 `_TokenTrackerCallback`（BaseCallbackHandler）跟踪 LLM token 用量 |
| `src/core/agent/budget.py` | `ExecutionBudget` 新增 `input_tokens` / `output_tokens` 字段 |
| `src/core/agent/service.py` | `_stream_locked_turn` 的 done 事件携带 `context_tokens` |
| `src/core/context/models.py` | `AgentContextState` 新增 `context_tokens` 字段 |
| `src/core/finalization/service.py` | `TurnFinalizer.finalize()` 更新 `fast_state.context_tokens` |
| `src/core/state/migrations.py` | Schema v5：sessions 表新增 `context_tokens` 列 |
| `src/core/adapters/sqlite/session_store.py` | `save_fast_context()` 写入 `context_tokens` |
| `src/config/settings.py` | 新增 `MODEL_CONTEXT_LIMIT`（默认 128_000） |

### 3.3 认证错误

**症状**：TUI 启动后状态栏显示 `error: [-32602] INVALID PARAMS`。

**原因**：`core.ping` 在较新版本的 Core 中要求 `auth_token` 参数，而初始实现调用 `ping()` 时未传递 auth_token。

**修复**：`AsyncCoreClient.ping(auth_token)` 改为接收并传递 auth_token：

```python
async def ping(self, auth_token: str = "") -> dict[str, Any]:
    params: dict[str, Any] = {}
    if auth_token:
        params["auth_token"] = auth_token
    return await self.request("core.ping", params)
```

### 3.4 Schema 迁移健壮性

**问题**：添加 `context_tokens` 列的迁移在空数据库上运行时报错。

**原因**：`PRAGMA table_info` 在表不存在时返回空结果，后续列检查逻辑未防御。

**修复**：在 `migrations.py` 中增加 `if not columns: continue` 跳过不存在的表。

