# Workspace 隔离设计决策

> 文档状态：Current Decision
> 权威范围：用户级 daemon 下 Workspace、Session、记忆、工具和路径的隔离方案与取舍
> 维护触发：Workspace 识别、Session 归属、路径安全或跨 Workspace 能力变化

## 本文负责

- Workspace 识别、Session/Memory/Tool 归属和路径安全隔离的方案取舍。

## 本文不负责

- 不维护历史 PostgreSQL Schema 或迁移步骤。
- 不定义当前数据库表和 API 字段。


当前状态、事务和恢复机制见
[本地数据库设计与一致性机制](/docs/architecture/database-state-and-consistency.md)；记忆加载策略见
[记忆管理与加载机制](/docs/architecture/memory-management.md)。

## 设计目标

系统采用“用户级后台服务 + Workspace 级业务隔离”：

```text
用户级 Core daemon
    -> 已认证 JSON-RPC 请求
        -> WorkspaceContext
            -> SessionContext
                -> Agent graph / Tools / Skills / Memory / Events
```

Core daemon 不属于任何项目。每次 `agent.chat` 必须声明目标 Workspace，Session、
长期记忆和工具只能使用该 Workspace。

## 方案选择

### 用户级 daemon 与严格 Workspace 隔离

旧实现将 `.agent_runtime` 放在当前目录，不同目录会找到不同 PID 和 token。用户级
runtime 允许 CLI、未来 TUI 和 IDE 插件复用同一个 Core 进程，这接近 Codex 的
用户级服务设计。

编码 Agent 的文件、命令、Skill、Session 和记忆又具有明确项目边界，因此默认按
最近 Git 根目录识别 Workspace，并严格过滤数据。这接近 Claude Code 的项目会话
设计。

最终模型是：

```text
Codex 式用户级后台服务
+ Claude Code 式 Workspace 业务隔离
```

### Session UUID 与名称分离

用户继续使用 `default`、`feature-x` 等名称，数据库内部使用 UUID：

```text
workspace A / default -> session UUID A
workspace B / default -> session UUID B
```

每个 Workspace 可以拥有同名 Session，消息、事件和会话锁使用不会冲突的内部身份。

### Bootstrap Memory

只根据第一条问题检索记忆容易失败，例如“你还记得我吗”通常不会匹配项目事实。
新 Session 第一轮因此加载当前 Workspace 最多 4 条高重要度近期记忆，并合并最多
6 条当前问题相关记忆。后续轮次只加载相关记忆。

系统不会注入其他 Workspace 的记忆，也不会注入全部长期记忆。

### 暂不直接实现 pgvector

向量检索需要明确 embedding 模型、维度、回填和重建策略。当前先建立
`retrieve_bootstrap()` 与 `retrieve_relevant()` 边界，完成隔离和可靠兜底。
未来替换为混合向量检索时，不需要修改 Agent 应用服务。

## 设计模式与依赖原则

- **Composition Root**：进程入口选择 Workspace、状态、Runtime 和 Transport 的具体实现，业务模块不自行创建共享资源。
- **Repository / Port**：Workspace 与 Session 身份通过领域接口访问，不向 Agent 暴露表结构或数据库连接。
- **Factory + Registry**：每个 Workspace 创建绑定自身根目录的 Runtime，并按 Workspace 身份缓存；运行时不修改全局 cwd。
- **Strategy**：Bootstrap 与 Relevant 记忆采用可替换检索策略，关键词或向量实现位于 Adapter 边界。

当前组件与接口关系由[接口驱动的 Core](/docs/architecture/interface-driven-core.md)和
[Agent 执行架构](/docs/architecture/agent-execution-architecture.md)维护。

## Workspace 身份数据流

```text
CLI 发现 Git 根目录或当前目录
  -> RPC 携带 workspace_root + session_name
  -> Core 规范化并验证 Workspace
  -> 解析 Workspace 内部身份与 Session UUID
  -> 选择 Workspace 绑定的 Runtime
  -> 后续 Agent、工具、Skill 和记忆操作只使用该 WorkspaceContext
```

Turn 执行和状态提交不属于 Workspace 决策，分别见[Agent 执行架构](/docs/architecture/agent-execution-architecture.md)
和[数据库状态与一致性](/docs/architecture/database-state-and-consistency.md)。
## 路径与安全边界

- `workspace_root` 必须存在且为目录。
- 文件工具只接受相对路径。
- 文件和 Skill 路径执行 `resolve()` 后必须仍位于 Workspace 内。
- Docker 工具只复制当前 Workspace，并跳过 `.env`、`.git` 和符号链接。
- 未通过本地 token 鉴权的请求不能注册 Workspace 或执行工具。

首次已认证请求自动注册 Workspace。当前版本没有单独的 `workspace trust` 命令。

## 用户级配置边界

用户级 daemon 的配置和 runtime 文件不能依赖启动命令所在目录，否则跨目录客户端无法发现同一服务。
具体平台路径、覆盖变量和初始化命令由[配置参考](/docs/reference/configuration-reference.md)与
[部署指南](/docs/operations/deployment.md)维护。
## 历史迁移背景

旧 PostgreSQL Schema、Workspace 数据归属迁移和迁移校验方案已移至
[Workspace PostgreSQL 迁移历史设计](/docs/history/workspace-postgres-migration-design.md)。

当前系统以本地 `state.db` 为业务权威来源；本文只保留仍然有效的 Workspace 隔离决策。
## 实现历史

全局 Graph、后台身份传播、旧记忆归属和跨目录服务发现等已解决问题迁到
[Workspace 隔离重构记录](/docs/history/workspace-isolation-refactor-notes.md)。历史说明不能作为当前模块接口依据。

## 当前能力边界

已支持和未支持的 Workspace 能力由[路线图与已知限制](/docs/product/roadmap-and-known-limitations.md)
统一维护，避免 Decision 复制易变化的功能清单。