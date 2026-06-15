# 升级与回滚

> 文档状态：Current
> 权威范围：当前单机用户级部署的版本升级、验证和人工回滚流程
> 维护触发：发布方式、Schema migration、配置兼容性或部署模型变化

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

Core 启动时会执行可重复的加法 Schema migration。Migration 必须在事务中完成；失败时 Core
应拒绝继续提供服务，而不是带着部分 Schema 运行。

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

不要尝试手工删除新列、降低 Schema version 或只恢复一个 SQLite 文件。

## 6. 协议兼容性

当前没有版本协商。升级时应同步升级 CLI 与 Core；不同版本混用属于不受支持状态。
协议变更要求见[协议兼容性](/docs/api/protocol-compatibility.md)。

## 7. 当前缺口

- 没有自动发布、版本签名和 release artifact。
- 没有自动状态快照和回滚命令。
- 没有 downgrade migration。
- 没有跨版本兼容矩阵和协议协商。
- 没有生产级滚动升级或高可用部署。

这些能力在引入正式分发或多用户部署前必须补齐。
