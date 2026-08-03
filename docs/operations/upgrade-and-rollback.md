# 升级与回滚

> 文档状态：Current
> 权威范围：当前单机用户级部署的版本升级、验证和人工回滚流程
> 维护触发：发布方式、Schema migration、配置兼容性或部署模型变化

## 本文负责

- 单机用户级部署的升级前检查、升级验证和人工回滚。

## 本文不负责

- 不定义维护者发布门禁；见 Release Process。
- 不复制专项 PostgreSQL 数据迁移步骤。


## 1. 当前发布模型

项目当前通过 editable install 或本地 Python 包运行，没有正式版本发布流水线、安装器或自动回滚。
CLI 与 Core 使用同一代码版本是当前兼容性要求。

升级可能同时改变：

- Python 代码和依赖。
- JSON-RPC、流式事件或 CLI 命令。
- 本地 SQLite Schema。
- 用户级配置和默认值。

因此，升级前必须创建本地状态备份，并在停止 daemon 后更换代码。

## 2. 升级前检查

1. 阅读目标版本的变更说明、已知限制和迁移要求。
2. 确认当前 Session 没有需要立即恢复的执行。
3. 停止 daemon。
4. 按[备份与恢复](/docs/operations/backup-and-restore.md)备份整个状态目录。
5. 安全备份用户级 `.env`。
6. 记录当前 Git commit、Python 版本和关键依赖版本。

## 3. 升级流程

```shell
learn-agent stop
python -m pip install -e .
learn-agent start
learn-agent status
```

若 `.env.example` 增加了新参数，人工合并到项目 `.env` 后同步用户配置：

```shell
learn-agent-core init-user-config --from-env .env --force
```

然后重新启动 Core。

Core 启动时会执行可重复的加法 Schema migration。实际调用链为：

```text
CoreApp.start()
  -> AgentTurnService.initialize()
  -> LocalStateStore.initialize()
  -> LocalStateDatabase.initialize()
```

`LocalStateDatabase.initialize()` 在显式 SQLite 事务中创建 Schema 并执行加法 Migration。异常会回滚并
继续向上传播；`CoreApp.start()` 捕获启动异常、关闭已创建资源并重新抛出，因此 Transport 不会在
Migration 失败后继续提供服务。相关回滚行为由本地 Schema Migration 集成测试覆盖。

## 4. 升级后验收

至少执行：

```shell
learn-agent status
learn-agent session status --session default
learn-agent trace --limit 50
python -B -m unittest discover -s tests -t . -v
```

同时确认：

- CLI 与 Core 使用相同版本。
- 旧 Session、消息和记忆仍可读取。
- 新对话能够收到 token、完成响应并被持久化。
- 后台维护任务没有持续失败。
- 无意启用的 PostgreSQL 可选能力没有成为启动依赖。

## 5. 回滚原则

代码回滚和数据回滚必须一起考虑。若新版本已经升级了 SQLite Schema，旧代码不一定能读取新
Schema；只切换 Git 分支可能无法完成回滚。

可靠回滚流程：

1. 停止 daemon。
2. 保存升级后现场用于调查。
3. 恢复升级前的整个状态目录快照。
4. 恢复升级前代码和依赖。
5. 恢复相匹配的用户级配置。
6. 启动并执行健康检查。

不要自行猜测 SQL 删除新列或降低 Schema version。Schema v11 的资源活动表属于可丢弃派生数据，确需从 v11 回到 v10 时，应使用 `learn-agent-core rollback-local-state --from-version 11 --to-version 10 --apply`。命令会拒绝运行中的 daemon，并与 daemon、`migrate-local-state --apply`、`gc-artifacts` 共享 `state.db.operation.lock` 跨进程排他锁。在写备份前命令验证版本转换，只允许 `v11 -> v10`，再通过原子排他创建生成带微秒时间戳和随机后缀的完整数据库备份。备份复制与 SQLite `quick_check` 各自受 30 秒截止约束；失败或不完整的备份文件会被删除，原数据库不会进入降级事务。其他版本仍应恢复完整状态目录快照。

## 6. 协议兼容性

当前没有版本协商。升级时应同步升级 CLI 与 Core；不同版本混用属于不受支持状态。
协议变更要求见[协议兼容性](/docs/api/protocol-compatibility.md)。

## 7. 当前缺口

- 没有自动发布、版本签名和 release artifact。
- 没有通用自动状态快照；只有 v11 到 v10 提供受控回滚命令。
- 除 v11 到 v10 外，没有通用跨版本 downgrade migration。
- 没有跨版本兼容矩阵和协议协商。
- 没有生产级滚动升级或高可用部署。

这些能力在引入正式分发或多用户部署前必须补齐。
