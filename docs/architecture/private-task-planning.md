# Agent 私有任务规划

> 文档状态：Current
> 权威范围：goal 模式下父 Agent 私有任务系统的当前实现
> 维护触发：任务工具、Execution、State Schema、Agent Prompt 或 goal 模式发生变化

## 本文负责

- goal 模式下父 Agent 私有任务计划的领域模型、工具和 Execution 绑定。

## 本文不负责

- 不提供用户可见任务 CRUD 契约。
- 不解释通用 Agent Loop 或数据库 Schema。


## 1. 解决的问题

普通 `learn-agent chat` 适合短问题和单步操作。复杂目标不同：它可能需要阅读多个文件、修改代码、运行测试、根据结果调整方案，并且可能因为步数预算暂停后再 `session resume`。

如果要求用户先把目标拆成很多小指令，会把认知负担转移给用户。任务规划系统的目标是：

- 用户只给最终目标。
- 父 Agent 在 goal 模式下自主决定是否拆解任务。
- 任务计划作为 Agent 的私有工作记忆，不变成用户要管理的项目清单。
- 跨 Slice、resume 和 daemon 重启后，任务计划仍属于同一个 Execution。

因此任务系统只在显式 goal 模式开启：

```shell
learn-agent chat --goal "重构这部分代码并补测试"
```

普通 chat 不暴露 `task_*` 工具，父 Agent 也不会看到任务规划提示词。

## 2. 核心对象

```text
Workspace
  -> Session
      -> Execution          一个可恢复的用户目标
          -> Slice          一次有步数上限的 LangGraph 执行片段
          -> ExecutionTask  父 Agent 的私有任务计划
```

关键点：

- `Execution` 是任务归属边界。一次 goal 可跨多个 `chat/resume` 请求继续执行，所以任务不能绑定短生命周期的 `run_id` 或 Slice。
- `task_key` 是 LLM 使用的稳定语义键，例如 `inspect_structure`。内部仍使用 UUID `task_id`，但不暴露给模型。
- 任务计划不会控制 AgentLoop。它是辅助记忆，不是外部调度器；Agent 仍可以在必要时直接回答或调整计划。

## 3. 数据库设计

任务数据写入本地权威 `state.db`，Schema 版本为 v4。

```text
execution_tasks
  task_id       内部 UUID
  execution_id 归属 Execution
  task_key     LLM 使用的语义键
  subject      简短主题
  description 详细说明
  status       pending / in_progress / completed / cancelled
  notes        进度备注
  ordinal      计划顺序
  version      审计版本号，每次更新递增

execution_task_dependencies
  execution_id
  task_id
  depends_on_task_id
```

依赖关系使用独立表，而不是 JSON 字段。原因：

- 可以用外键保证依赖只发生在同一个 Execution 内。
- 可以保留完整依赖关系，不需要在依赖完成后删除记录。
- 是否 blocked 由查询时动态计算：依赖任务是 `pending` 或 `in_progress` 时阻塞；依赖任务是 `completed` 或 `cancelled` 时不再阻塞。

`execution_tasks` 上同时保留 `PRIMARY KEY(task_id)` 和 `UNIQUE(execution_id, task_id)`。后者不是为了表达新的业务语义，而是为了让 `execution_task_dependencies` 可以使用 `(execution_id, task_id)` 复合外键。这样依赖表即使同时保存两个任务 ID，也必须证明它们属于同一个 Execution。

`version` 当前只用于审计和排障，表示任务被更新过多少次；首版没有把它作为乐观锁或 CAS 条件使用。原因是当前任务工具调用仍在单个 Agent 执行链中串行发生。后续如果引入任务级并发写入、工具调用重放或公开任务编辑 API，再把 `version` 升级为写冲突检测条件。

Repository 会拒绝：

- 自身依赖。
- 不存在的依赖。
- 跨 Execution 依赖。
- 循环依赖。
- 超过 `LEARN_AGENT_TASK_MAX_PER_EXECUTION` 的任务数量。

实现上，任务模块拆成三层，避免 `TaskRepository` 继续膨胀：

| 模块 | 职责 |
|---|---|
| `TaskRepository` | 负责 `task_plan` / `task_update` 的事务编排、Execution 身份校验和写入顺序 |
| `TaskQueryStore` | 负责任务行读取、依赖关系读取、blocked/ready 计算和 `ExecutionTask` 装配 |
| `TaskPlanValidator` | 负责不依赖数据库的纯规则校验 |

`TaskPlanValidator` 当前覆盖：

- `task_key` 格式校验。
- subject / description / notes 长度限制。
- 单次 plan 的重复 key 检查。
- 依赖 key 校验。
- 依赖图环路检查。

这样后续如果新增文件或 PostgreSQL 任务后端，可以复用同一套任务计划校验和查询装配思路，而不是在每个 repository 中复制规则。

## 4. 工具设计

goal 模式下父 Agent 可见四个普通 LangChain 工具：

| 工具 | 作用 |
|---|---|
| `task_plan` | 原子创建或更新一批任务和依赖图 |
| `task_update` | 更新单项任务的状态、说明、备注或依赖，并返回刷新后的完整紧凑计划视图 |
| `task_list` | 返回当前 Execution 的紧凑计划视图 |
| `task_get` | 读取单项任务详情 |

这些工具属于 `ToolRisk.INTERNAL_STATE`：

- 会计入工具调用总硬上限。
- 不消耗命令执行额度。
- 不消耗子 Agent 委派额度。
- 不产生 Workspace 文件副作用。

子 Agent 不会收到任务工具。子 Agent 的定位是短生命周期局部执行者，任务规划属于父 Agent 的整体认知工作。

`task_plan` 和 `task_update` 的返回内容都应包含当前 Execution 的紧凑任务清单。这不是公开任务 API，而是为了让 CLI/TUI 在 goal 模式下能及时显示父 Agent 的计划变化。TUI 会把这些结果折叠成一个“最新任务进度”状态块并原地更新，避免每次 `task_update` 都追加一份完整清单造成刷屏。

## 5. goal 模式如何切换工具

`WorkspaceRuntime` 按 Workspace 缓存两套父 Agent graph：

```text
runtime.graph       普通 chat graph，不含 task_* 工具
runtime.goal_graph  goal graph，含 task_* 工具
```

调用链：

```text
CLI --goal
  -> ChatParams.goal_mode = true
  -> AgentHandlers.chat()
  -> AgentTurnService.stream_turn(goal_mode=True)
  -> ExecutionRepository.begin(..., goal_mode=True)
  -> runtime.goal_graph
```

resume 时不依赖 CLI 再次传 `--goal`。Core 从 `executions.goal_mode` 读取原 Execution 的模式：

```text
session.resume
  -> ExecutionRepository.resume()
  -> pending.goal_mode
  -> runtime.goal_graph 或 runtime.graph
```

这样 goal 执行被预算暂停后，恢复时仍能继续访问原任务计划。

## 6. ToolRuntime 上下文

Graph 是按 Workspace 缓存的，不能把某个 Execution ID 写进全局变量或工具闭包。当前实现使用 LangGraph `ToolRuntime` 注入：

```text
AgentTurnService
  -> ToolExecutionContext(workspace_id, session_id, execution_id)
  -> graph.stream(..., context=ToolExecutionContext)
  -> task tool runtime.context
  -> TaskPlanningService
  -> TaskRepository
```

`ToolRuntime` 参数不会进入 LLM 可见的工具 schema。模型只能看到业务参数，例如 `task_key`、`tasks`、`status`。

如果缺少 Execution 上下文，任务工具返回明确错误，不访问数据库。

## 7. 当前边界

当前已实现：

- goal 模式下私有任务工具。
- Execution 级任务持久化。
- 依赖校验和动态 blocked 计算。
- resume 继承 goal 模式。
- 父 Agent 可见、子 Agent 不可见。

当前不实现：

- 用户查询或编辑任务的 CLI/RPC。
- 自动任务调度器。
- 任务并行执行。
- 任务级 retry 状态机。
- 任务作为最终回答的强制门禁。

这些边界是有意保留的。任务系统是 Agent 的认知辅助，不是用户项目管理系统。
