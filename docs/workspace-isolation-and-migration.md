# Workspace 隔离与数据库迁移设计

> 当前记忆数据分层、每轮加载顺序、提取策略与一致性边界见
> [`memory-management.md`](memory-management.md)。

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
未来替换为混合向量检索时，不需要修改 AgentTurnService。

## 设计模式与依赖原则

### Composition Root

`CoreApp` 是组合根，负责创建连接池、WorkspaceRepository、WorkspaceRuntimeRegistry
和 AgentTurnService。Transport、Handler 与 Agent 业务保持单向依赖。

### Repository

- `WorkspaceRepository`：注册 Workspace、解析 Workspace 内 Session。
- `SessionRepository`：读取和更新短期上下文。
- `MessageRepository`：归档完整消息。
- `MemoryRepository`：按 Workspace 检索和保存长期记忆。
- `PostgresEventSink`：批量写入结构化事件。

### Factory 与 Registry

`WorkspaceRuntimeFactory` 为一个 Workspace 创建工具集、SkillStore、子 Agent 和父
Agent graph；`WorkspaceRuntimeRegistry` 按 Workspace UUID 缓存。

工具通过闭包永久绑定 Workspace 根目录，不修改全局 cwd 或 `WORKSPACE_DIR`，因此
多个 Workspace 可以安全并发。

### Strategy

长期记忆检索分为 Bootstrap 与 Relevant 两种策略。当前使用关键词与重要度排序，
未来可增加 pgvector 混合检索。

## 请求数据流

```text
learn-agent chat --session default
  -> CLI 向上寻找最近 Git 根目录
  -> agent.chat(workspace_root, session_name, message)
  -> Core 验证路径并注册 Workspace
  -> 解析或创建 Workspace 内 Session UUID
  -> 获取 WorkspaceRuntime
  -> 获取 Session UUID 锁
  -> 加载短期上下文与 Workspace 记忆
  -> 执行 Workspace 绑定 graph/tools
  -> 保存消息、上下文、记忆和事件
```

## 路径与安全边界

- `workspace_root` 必须存在且为目录。
- 文件工具只接受相对路径。
- 文件和 Skill 路径执行 `resolve()` 后必须仍位于 Workspace 内。
- Docker 工具只复制当前 Workspace，并跳过 `.env`、`.git` 和符号链接。
- 未通过本地 token 鉴权的请求不能注册 Workspace 或执行工具。
- SQL 值使用 psycopg 参数绑定；迁移表名使用 `psycopg.sql.Identifier`。

首次已认证请求自动注册 Workspace。当前版本没有单独的 `workspace trust` 命令。

## 用户级配置

系统使用 `platformdirs`：

```text
user config/learn-agent/.env
user state/learn-agent/runtime/daemon.pid
user state/learn-agent/runtime/daemon.token
user state/learn-agent/runtime/daemon.log
user data/learn-agent/backups/*.dump
```

可通过 `LEARN_AGENT_ENV_FILE` 和 `LEARN_AGENT_RUNTIME_DIR` 覆盖默认位置。

显式初始化密钥配置：

```powershell
learn-agent-core init-user-config --from-env .env
```

默认拒绝覆盖已有用户配置。

## 数据库结构

```text
agent_workspaces
  -> agent_sessions
       -> agent_messages
       -> agent_events

agent_workspaces
  -> agent_memories
       -> agent_memory_sources(workspace_id) -> agent_messages
```

主要约束：

- `UNIQUE(workspace_id, session_name)`
- 消息和 Session 事件同时保存 Workspace 与 Session UUID
- 长期记忆查询、更新和去重必须包含 `workspace_id`
- `agent_memory_sources` 使用关系表保存记忆来源，并通过 Workspace 复合外键阻止
  记忆关联其他 Workspace 的消息

## 显式迁移与恢复

旧数据库不会自动升级。检测到旧结构时，Core 拒绝启动并提示：

```powershell
learn-agent-core migrate-workspace `
  --workspace D:\Desktop_logo\github\myprojects\learn_langchain `
  --keep-session default
```

默认只执行 dry-run。正式迁移增加 `--apply`。

正式迁移要求 daemon 已停止，并在事务前创建完整 `pg_dump`：

1. 优先使用本机 `pg_dump`。
2. 不可用时通过 PostgreSQL Docker 容器执行。
3. 备份失败或为空时拒绝迁移。
4. 数据复制、校验和旧表删除位于同一事务。
5. 任意校验失败都会回滚。

本次真实迁移结果：

| 数据 | 迁移前 | 保留 |
|---|---:|---:|
| Sessions | 3 | 1 |
| Messages | 533 | 503 |
| Memories | 7 | 7 |
| Events | 1735 | 1611 |

完整备份位于用户级 backups 目录，可使用 `pg_restore` 恢复。

## 实现难点

### 导入期全局 Agent graph

旧 graph 在导入时绑定全局工具，工具又绑定 daemon 启动 cwd。修改全局 cwd 会在并发
请求间产生竞态。现在由 WorkspaceRuntimeFactory 为每个 Workspace 单独创建 graph
和工具闭包。

### 后台记忆提取

线程池任务不会自动拥有完整业务身份。后台提取现在显式携带 Workspace、Session、
Turn 和 Run，并在工作线程重建事件上下文。

### 旧长期记忆归属

旧记忆没有 Workspace 字段，只能通过 `source_message_ids -> agent_messages` 判断来源。
迁移仅保留来源于 `default` 的记忆，并将来源规范化到关系表。

### 跨目录服务发现

只把 daemon 放到后台不够。runtime 文件、密钥配置、Session、工具目录和记忆查询都
必须同时去除对 daemon cwd 的依赖。

## 当前功能边界

当前支持：

- 任意目录访问同一用户级 daemon。
- Git 根目录自动识别。
- Workspace 内同名 Session。
- Workspace 隔离的文件、Skill、命令、Session、记忆和事件。
- 新 Session Bootstrap Memory。
- 显式、可备份、可回滚的旧数据库迁移。

当前不支持：

- 跨 Workspace 恢复 Session。
- Workspace relocate。
- Session/Workspace 列表命令。
- 全局用户记忆。
- pgvector 语义检索。
- WorkspaceRuntime 缓存淘汰。
