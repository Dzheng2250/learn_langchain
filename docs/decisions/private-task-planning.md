# 私有任务规划设计决策

> 文档状态：Current
> 权威范围：Agent 私有任务规划系统的方案取舍
> 维护触发：任务系统定位、存储方式、工具边界或 goal 模式发生变化

## 1. 决策

任务规划只作为 goal 模式下父 Agent 的私有认知工具：

- 普通 chat 不暴露任务工具。
- goal 模式暴露 `task_plan/task_update/task_list/task_get`。
- 任务绑定 Execution。
- 任务存储在本地权威 `state.db`。
- 不提供用户可见任务 CRUD RPC。

## 2. 为什么不是默认开启

任务工具会占用模型注意力。对简单问题来说，让模型看到任务工具只会增加选择空间，可能导致“本来一句话能回答，却先创建计划”的过度行为。

因此当前采用显式 goal 模式：

```shell
learn-agent chat --goal "完成一个较大的目标"
```

这个设计让能力和成本匹配：

- 普通问答保持轻量。
- 多步骤目标获得可恢复计划。
- resume 可以继续读取同一个 Execution 的任务状态。

## 3. 借鉴 KamaClaude 的部分

借鉴点：

- 把任务能力做成普通工具，而不是外部控制面板。
- 让 LLM 自己决定是否规划、如何规划、何时更新。
- 提供 `create/update/list/get` 一组最小任务操作。
- 任务依赖用于表达执行顺序。

这与当前项目的工具体系兼容：任务工具与文件工具、命令工具、委派工具一样进入 LangChain tool schema。

## 4. 没有照搬的部分

### 不按 run 写 `.tasks/*.json`

当前项目已有可恢复 Execution：

```text
Execution
  -> Slice 1
  -> Slice 2
  -> resume 后 Slice 3
```

如果任务绑定 run，resume 后会丢失上一段计划上下文。绑定 Execution 更符合当前恢复模型。

### 不使用整数 ID

整数 ID 对人类和 LLM 都容易混淆，尤其是计划被插入、取消、重排之后。当前要求模型使用语义 `task_key`：

```text
inspect_structure
update_tests
run_validation
```

内部 UUID 只用于数据库外键，不暴露给模型。

### 不用 JSON 依赖字段

依赖关系放在独立表里。这样可以用外键防止跨 Execution 依赖，也能在查询时动态判断 blocked 状态。

### 不让任务成为 AgentLoop 控制器

任务计划可能错误。若系统强制“未完成任务不能回答”，模型一旦规划错误就可能卡死。当前策略是：

- Prompt 要求 Agent 尽量维护任务状态。
- 系统不强制阻止最终回答。
- 任务记录用于恢复和审计，而不是硬性调度。

## 5. 涉及的设计模式

### Repository

`TaskRepository` 只负责 SQLite 读写、事务和约束校验。它不关心工具输出格式。

### Application Service

`TaskPlanningService` 负责把工具输入转换成领域模型，并把结果格式化给 LLM。

### Factory

`create_task_tools()` 根据一个服务实例创建 LangChain 工具。`WorkspaceRuntimeFactory` 决定普通 graph 和 goal graph 如何组装。

### Dependency Injection

任务工具不从全局变量读取 Execution ID，而是通过 `ToolRuntime.context` 接收 `ToolExecutionContext`。

### Unit of Work

`task_plan` 在一个 SQLite 事务中提交整批任务和依赖。任何校验失败都会整体回滚。

## 6. 风险

| 风险 | 当前缓解 |
|---|---|
| 模型在 goal 模式中滥用任务工具 | Prompt 要求简单任务不建计划，且普通 chat 不暴露工具 |
| 计划错误导致执行偏离 | 用户最新请求优先，任务不作为硬控制器 |
| resume 后工具不可用 | `executions.goal_mode` 持久化，resume 自动选择 goal graph |
| 跨 Execution 数据泄漏 | Repository 每次校验 `workspace_id/session_id/execution_id` |
| 任务数量膨胀 | `LEARN_AGENT_TASK_MAX_PER_EXECUTION` 默认 40 |

## 7. 后续方向

可以考虑但当前不做：

- 只读任务状态查询 RPC，用于调试。
- goal 模式下任务摘要注入最终回答。
- 任务计划质量评估。
- 任务级 trace 聚合视图。
- 更细粒度的任务工具预算。
