# 本地优先状态与可恢复执行测试方案

> 文档状态：Current
> 权威范围：本地状态、恢复执行和一致性机制的专项测试方案
> 维护触发：状态模型、恢复流程或相关非功能目标变化

## 本文负责

- 本地状态、Turn 提交、后台维护和可恢复执行的专项测试矩阵。

## 本文不负责

- 不解释实现原理；见 State Architecture。
- 不替代通用测试目录和运行指南。


## 自动回归门槛

| 测试 | 验证内容 |
|---|---|
| `test_completed_turn_is_atomic_and_updates_branch_head` | 消息与 Session 状态在同一事务提交 |
| `test_graph_continues_from_checkpoint_after_slice_limit` | 达到 Slice 步数上限后从 checkpoint 继续，不从头执行 |
| `test_one_pending_execution_per_session` | 一个 Session 最多存在一个未完成执行 |
| `test_execution_repository_persists_slice_budget_usage` | Slice 和 Grant 预算使用量可恢复、可审计 |
| `test_parallel_tool_slot_is_bounded` | 同一 Grant 的并行工具数受限 |
| `test_local_source_prune_rolls_back_when_validation_fails` | PostgreSQL 清理校验失败时完整回滚 |
| `test_notification_failure_is_recorded_once_without_cancelling_turn` | 客户端断开被记录，并触发 Slice 后暂停信号 |

完整回归命令：

```powershell
python -B -m unittest discover -s tests -v
git diff --check
```

## 真实迁移验收

真实迁移不是普通单元测试。它会接触用户现有数据，因此必须按以下顺序人工验收：

1. 确认 daemon 已停止。
2. 执行 dry-run 并核对保留、删除数量。
3. 确认完整 PostgreSQL 备份存在且非空。
4. 执行 `--apply --prune-source`。
5. 检查 SQLite 外键和行数。
6. 检查 PostgreSQL 只保留目标 `default` 的关联数据。
7. 启动 daemon，验证历史加载、长期记忆和新对话。

## 尚未实现的自动测试

以下测试需要稳定的性能环境或进程级故障注入，本轮不把它们伪装成普通单元测试：

- **崩溃一致性测试**：在 SQLite commit、checkpoint 写入和 Artifact 写入的不同阶段强制终止 Core，再验证恢复结果。
- **持续性能门禁**：统计末 token 到 CLI 恢复输入、SQLite commit、Telemetry publish 的 p95/p99。
- **真实慢磁盘测试**：模拟 JSONL 和 Artifact 目录变慢，确认有界队列不会拖慢前端。
- **真实阻塞工具超时**：验证一个不响应的外部命令在超时后被子进程机制终止。

可靠实施方式：

1. 使用独立临时用户数据目录，避免污染开发者状态。
2. 使用可控故障注入点，而不是随机杀进程。
3. 每个测试记录机器、Python、SQLite 和并发参数。
4. 性能测试使用多轮预热和分位数，不以单次耗时作为结论。
