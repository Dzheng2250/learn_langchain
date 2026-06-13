# 非功能性测试与验收方案

本文定义延迟、并发、故障隔离和后台任务的测试方法。对应需求见
[`non-functional-requirements.md`](non-functional-requirements.md)。

## 1. 测试原则

非功能测试必须满足：

1. 使用可控的 Fake、故障注入或本地容器，不依赖公网模型延迟。
2. 每项测试区分前台输出路径与后台最终提交路径。
3. 延迟测试重复运行并报告 p50、p95、p99，而不是只断言一次执行时间。
4. 测试设置明确超时，失败时不能无限等待。
5. 资源隔离测试必须同时运行至少两个不同 Session。
6. 性能阈值用于发现架构回归；共享 CI 噪声较大时，严格阈值应在专用环境执行。

## 2. 测试分层

| 层级 | 目的 | 默认 CI |
|---|---|---|
| 单元测试 | 验证非阻塞调用、队列边界、失败隔离 | 必须运行 |
| 组件测试 | 验证 AgentService、EventBus、Finalizer 的并发行为 | 必须运行 |
| 本地集成测试 | 验证 TCP、CLI、PostgreSQL 和 daemon 完整链路 | 建议运行 |
| 基准测试 | 统计 p50/p95/p99 和资源占用 | 专用环境运行 |
| 长时间稳定性测试 | 验证队列增长、连接泄漏和内存增长 | 发布前运行 |

## 3. 必须补充的自动测试

### 3.1 慢 Telemetry 数据库不影响 token

构造：

- `BufferedEventSink` 下游 `emit_batch()` 固定阻塞 500 ms。
- Agent 连续产生多个 token。
- 记录每次 token callback 时间。

验收：

- token callback 间隔不随 500 ms 数据库阻塞增加。
- Agent 输出正常完成。
- Telemetry 后台最终写入或记录失败。

### 3.2 Telemetry 队列满不阻塞输出

构造：

- 队列容量设为 1。
- 下游 Sink 持续阻塞。
- 快速发布大量事件并同时产生 token。

验收：

- `emit_event()` 在限定时间内返回。
- token 输出继续。
- 存在可观测的丢弃计数或调试记录。
- 不出现无界内存增长。

### 3.3 IO 型 Sink 不得进入前台关键路径

分别为 JSONL 和 Console Sink 注入 500 ms 写入延迟。

验收：

- 默认生产配置下，慢 IO Sink 不增加 token 转发延迟。
- 如果某 Sink 未被缓冲，测试必须失败并指出配置风险。

### 3.4 普通会话保存不延迟前端释放

目标架构实现后台 Finalizer 后，构造：

- 模型立即产生完整回答。
- `commit_turn()` 固定阻塞 2 秒。
- CLI 记录最后 token 和重新获得输入权的时间。

验收：

- CLI 在最后 token 后 250 ms 内恢复输入。
- 后台提交仍然继续并最终完成。
- 不显示“正在保存会话”。

该测试在当前实现下预期失败，应在引入后台提交后转为强制回归测试。

### 3.5 同一 Session 保持一致性

构造：

- 第一轮回答后，后台提交阻塞。
- 用户立即发起同一 Session 第二轮。

验收：

- 第一轮 CLI 已恢复输入。
- 第二轮在 Core 内部等待第一轮提交。
- 第二轮加载到第一轮已提交状态。
- 两轮 `turn_index` 连续且无覆盖。

### 3.6 不同 Session 不互相阻塞

构造：

- Session A 的提交或数据库读取阻塞 2 秒。
- 同时执行 Session B。

验收：

- Session B 的 token 输出延迟增量符合
  [`different_session_interference_ms`](non-functional-requirements.md#2-延迟目标)。
- Session B 不等待 Session A 的 Session 锁或后台提交。

### 3.7 上下文压缩不影响已开始的输出

构造：

- 注入固定阻塞 5 秒的总结模型。
- 触发上下文压缩。

验收：

- 已开始的回答 token 不被压缩任务打断。
- 目标架构下，普通回答结束后 CLI 不等待压缩。
- 压缩结果通过 Session version/CAS 检查，旧结果不能覆盖新状态。

### 3.8 慢客户端与断线

构造：

- Socket `drain()` 长时间不返回，或客户端停止读取。
- 另一连接正常请求 `core.ping` 和 Agent chat。

验收：

- 慢连接达到发送超时后停止通知。
- 对应 Turn 继续执行后台保存。
- 其他连接保持正常。
- Agent worker 不被无限占用。

### 3.9 正常关闭

构造：

- 后台会话提交、Telemetry 和记忆任务均存在待处理工作。
- 请求 `core.shutdown`。

验收：

- Core 停止接收新请求。
- 在配置超时内 drain 关键会话提交。
- best-effort Telemetry 可在超时后放弃。
- 不先关闭数据库池再等待依赖数据库的任务。

## 4. 延迟基准方法

每个延迟基准至少执行：

```text
warmup: 20 次
sample: 200 次
并发度: 1、4、8
报告: p50、p95、p99、最大值
```

建议测量时间点：

```text
t_request_received
t_first_token_generated
t_token_forward_started
t_token_forward_finished
t_last_visible_token
t_frontend_released
t_commit_started
t_commit_finished
t_maintenance_finished
```

由此计算：

```text
stream_forward_latency = t_token_forward_started - t_token_generated
response_release_latency = t_frontend_released - t_last_visible_token
ordinary_commit_latency = t_commit_finished - t_commit_started
maintenance_latency = t_maintenance_finished - t_commit_finished
```

## 5. 故障注入方式

优先使用可控替身，不在测试中依赖真实故障：

```python
class SlowBatchSink:
    def emit_batch(self, events):
        time.sleep(0.5)


class SlowCommitStore:
    def commit_turn(self, ...):
        time.sleep(2)
```

数据库集成测试可以使用：

- PostgreSQL `pg_sleep()` 模拟慢语句。
- 小连接池模拟连接竞争。
- `statement_timeout` 验证有限失败。
- 暂停测试容器模拟数据库不可用。

不得通过破坏真实开发数据库执行故障测试。

## 6. CI 与发布门禁

### 每次提交必须运行

```shell
python -B -m unittest discover -s tests -v
git diff --check
```

并强制覆盖：

- EventBus 和 Sink 失败隔离。
- 队列满不阻塞。
- 同 Session 顺序。
- 不同 Session 并行。
- Socket 并发写入完整性。
- Core 正常关闭顺序。

### 架构变更后必须运行

- PostgreSQL 容器集成测试。
- 慢数据库和慢客户端故障注入。
- 200 次样本延迟基准。
- 至少 30 分钟稳定性测试。

以下改动必须视为可能引入延迟回归：

- 在 token callback 或 `EventBus.publish()` 中增加 IO。
- 修改 Session 锁范围。
- 修改 `done` 或最终 JSON-RPC 响应时机。
- 修改数据库连接池大小或用途。
- 新增同步记忆、压缩或 Telemetry 工作。

## 7. 当前测试覆盖与缺口

当前已有测试覆盖：

- EventBus Sink 失败不影响其他 Sink。
- Buffered Sink 批量写入和 flush。
- 同一 Session 串行、不同 Session 可并行。
- 客户端通知失败不取消 Turn。
- 并发 Socket 写入不会交叉。
- Core 关闭等待活动请求。

当前尚缺少：

- 慢 Telemetry 数据库对 token 延迟的断言。
- Telemetry 队列满时的延迟和丢弃计数测试。
- 普通会话后台提交与前端立即释放测试。
- 上下文压缩后台执行和版本冲突测试。
- Socket 发送超时测试。
- 业务数据库池与 Telemetry 池争用测试。
- p50/p95/p99 自动基准和 CI 回归门禁。

在这些测试落地前，不能声称系统已经满足完整的用户延迟非功能需求。
