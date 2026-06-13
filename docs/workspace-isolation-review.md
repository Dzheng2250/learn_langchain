# Workspace 隔离实施代码审查

> 文档状态：Historical Review Record
> 本文记录 Workspace 隔离首次实施后的审查结果；当前数据权威来源和恢复机制已经演进为本地
> `state.db` / `checkpoints.db` 架构。

## 审查范围

本次审查覆盖用户级 daemon、Workspace 身份解析、Session 和长期记忆隔离、
WorkspaceRuntime、文件与命令工具、事件上下文、数据库结构和旧数据迁移。

审查重点是：

- 是否存在跨 Workspace 数据或文件访问路径。
- 是否存在路径逃逸、SQL 注入、敏感配置泄漏。
- 后台线程、连接池和 daemon 关闭顺序是否可靠。
- CoreApp、Repository、Factory、Registry 和 Strategy 的职责边界是否清晰。
- 正式迁移是否具备备份、事务、校验和回滚能力。

## 已修复问题

### 高风险：全局 Workspace 与导入期 graph

旧设计中的工具、SkillStore 和 Agent graph 依赖 daemon 启动目录或全局变量。在并发
请求中切换这些全局状态会造成跨 Workspace 文件和工具访问。

修复方式：

- `WorkspaceRuntimeFactory` 为每个 Workspace 创建固定绑定的工具集和 graph。
- `WorkspaceRuntimeRegistry` 按 Workspace UUID 缓存 Runtime。
- 工具通过闭包持有不可变 Workspace 根目录，不修改全局 cwd。

### 高风险：Session 和记忆缺少 Workspace 身份

旧 Session 使用可重复字符串，长期记忆查询没有项目边界。

修复方式：

- Session 内部改为 UUID，并使用 `UNIQUE(workspace_id, session_name)`。
- 消息和 Session 事件使用 Workspace 与 Session 复合外键。
- 长期记忆的检索、更新和去重全部携带 `workspace_id`。
- 记忆来源表使用 Workspace 复合外键，数据库层拒绝跨 Workspace 来源关联。
- 新 Session 仅注入当前 Workspace 的 bootstrap 和相关记忆。

### 高风险：Docker 复制可能跟随嵌套符号链接

顶层符号链接原本会被跳过，但 `copytree()` 可能跟随子目录中的符号链接，将
Workspace 外部内容复制进容器沙箱。

修复方式：

- 改为显式递归复制。
- 任意层级均跳过符号链接、敏感目录和 `.pyc` 文件。
- 增加嵌套符号链接回归测试。

### 高风险：旧结构可能被隐式升级

隐式升级无法确保用户理解数据归属，也难以保证迁移前已有有效备份。

修复方式：

- `SchemaManager` 检测旧结构时拒绝启动 daemon。
- 迁移命令默认只执行 dry-run。
- 正式迁移必须先生成非空完整备份，再进入单个数据库事务。
- 数据复制后校验数量和外键，失败时由事务完整回滚。
- 动态 SQL 标识符使用 `psycopg.sql.Identifier`。

### 中风险：后台记忆提取缺少完整事件身份

线程池不会自动继承稳定的业务身份，可能导致记忆事件关联到错误的 turn。

修复方式：

- 后台任务显式携带 Workspace、Session、Turn 和 Run。
- 工作线程开始时重建事件上下文。
- daemon 关闭时等待记忆任务完成，再关闭事件 sink 和共享连接池。

### 中风险：WorkspaceRuntime 创建被全局锁串行化

如果在全局锁内构建 graph，不同 Workspace 的首次请求会相互阻塞。

修复方式：

- Registry 使用短时全局索引锁和按 Workspace 创建锁。
- 同一 Workspace 只创建一次，不同 Workspace 可以并行创建。

### 中风险：测试可能污染真实 Workspace 数据

数据库测试如果直接使用仓库根目录，会创建或删除真实 Workspace Session。

修复方式：

- 增加固定测试 Workspace fixture。
- 测试清理仅按测试 Workspace UUID 删除数据。

## 设计原则结论

当前主要依赖方向符合设计目标：

```text
CLI -> IPC <- Core
Transport -> Router -> Handler -> AgentTurnService
CoreApp -> Repository + RuntimeRegistry + AgentTurnService
WorkspaceRuntime -> Workspace-bound Graph + Tools + Skills
```

- 单一职责：Transport、Router、Handler、Agent service、Repository 和 Runtime factory
  各自处理独立问题。
- 依赖倒置：Handler 和 CoreApp 依赖协议边界，可在测试中注入替代实现。
- 接口隔离：RPC 层不暴露数据库、工具或 LangGraph 内部对象。
- 组合优于继承：WorkspaceRuntime 和 CoreApp 通过组合构建完整能力。
- 显式依赖：Workspace 身份通过参数和不可变上下文传递，不依赖可变全局状态。

## 验证结果

- 完整 unittest：78 项通过，1 项符号链接测试因当前 Windows 未授予创建链接权限而跳过。
- AST 解析检查通过。
- 真实数据库迁移成功，完整备份已生成。
- 跨目录 daemon 生命周期检查通过。
- 不同 Workspace 的 `default` Session 和记忆隔离检查通过。
- 正式迁移完成时数据库保留：
  - Workspace：1
  - Session：1
  - Messages：503
  - Memories：7
  - Memory sources：14
  - Events：1611（后续测试产生的观测事件不属于迁移保留基线）
- 记忆来源孤儿记录：0。

## 残余风险与后续方向

1. `WorkspaceRuntimeRegistry` 当前没有缓存淘汰。大量一次性 Workspace 会持续占用内存。
2. 关键词记忆检索对语义改写不敏感；后续可在现有 Retriever 边界增加 pgvector
   混合检索。
3. 已通过本地 token 认证的客户端可以自动注册任意本地目录；高安全场景应增加
   Workspace trust/allowlist。
4. Docker 工具会复制整个 Workspace，大型仓库启动命令较慢；后续可使用受控 bind
   mount、增量缓存或明确的文件清单。
5. 消息归档与 Session 上下文更新目前是两个事务。极端故障可能保留已归档消息但
   未推进 Session 上下文，后续可增加 turn 级事务协调或恢复标记。
6. 迁移器只支持当前已知旧结构和保留单一 Session；未来 schema 变更应采用连续版本
   迁移，而不是扩展此一次性迁移器。
7. 顶层 Python 包仍命名为 `src`，长期应迁移为正式包名，例如 `learn_agent`。
