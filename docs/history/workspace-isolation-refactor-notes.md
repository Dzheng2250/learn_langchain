# Workspace 隔离重构记录

> 文档状态：Historical
> 当前权威来源：[`/docs/decisions/workspace-isolation-and-migration.md`](/docs/decisions/workspace-isolation-and-migration.md)

本文保存 Workspace 隔离落地时解决的实现问题，不描述当前接口。

## 导入期全局 Graph

旧 Graph 在模块导入时绑定工具，工具又绑定 daemon 启动 cwd。修改全局 cwd 会在并发请求间产生
竞态。重构后由 Workspace Runtime 工厂为每个 Workspace 创建绑定自身上下文的 Graph 与工具。

## 后台身份传播

后台线程最初无法自动获得完整 Workspace、Session、Turn 和 Run 身份。重构后调用边界显式携带
身份，并通过 ContextVar 在 worker 中恢复观测上下文。

## 旧长期记忆归属

旧 PostgreSQL 记忆没有 Workspace 字段，只能通过来源消息反推归属。一次性迁移仅保留目标
Session 的来源关系；当前状态结构不再依赖该推断。

## 跨目录服务发现

仅把 daemon 放入后台仍不足以实现跨目录访问。runtime 文件、用户配置、Session 身份、工具根目录
和记忆查询都必须去除 daemon 启动 cwd 依赖。该问题推动了用户级 daemon 与 WorkspaceContext 分离。

具体旧数据库迁移方案见[Workspace PostgreSQL 迁移历史设计](/docs/history/workspace-postgres-migration-design.md)。