# 私有任务规划设计决策

> 文档状态：Current Decision
> 权威范围：Agent 私有任务规划系统的方案取舍
> 维护触发：任务系统定位、存储方式、工具边界或 goal 模式发生变化

## 本文负责

- 任务规划为何只在 goal 模式暴露给父 Agent，以及存储和身份方案的取舍。

## 本文不负责

- 不维护当前任务工具实现；见 Private Task Architecture。
- 不提供用户任务管理接口。


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

### Repository / Port

任务读写通过 Execution 作用域的存储接口完成；校验逻辑不依赖具体数据库。应用服务只表达“规划、更新、
读取任务”，不接触 SQL、连接或文件格式。

### Application Service

任务规划服务把工具输入转换为领域命令，执行依赖校验，再返回适合 LLM 消费的紧凑结果。工具本身只是
协议 Adapter，不承载任务规则。

### Factory 与 Registry

组合边界决定普通 Graph 与 goal Graph 暴露哪些工具。任务能力按运行模式装配，不通过工具内部的全局
开关临时禁用。

### Context Injection

任务工具从运行时上下文接收 Workspace、Session 和 Execution 身份。模型不管理数据库 UUID，也不能
通过参数指定其他 Execution。

### Unit of Work

批量计划及依赖图必须在一个事务抽象中提交；任何字段、依赖或环路校验失败都整体回滚。具体存储实现
由 Adapter 提供，不属于本决策。

当前模块与接口见[私有任务规划架构](/docs/architecture/private-task-planning.md)。

## 6. 风险

| 风险 | 决策级缓解原则 |
|---|---|
| 模型在 goal 模式中滥用任务工具 | 普通 chat 不暴露任务工具；Prompt 要求简单目标不建计划 |
| 计划错误导致执行偏离 | 用户最新请求优先，任务不是 AgentLoop 的硬控制器 |
| resume 后规划能力丢失 | Execution 持久化运行模式，恢复时选择同类 Graph |
| 跨 Execution 数据泄漏 | 所有任务端口都要求当前 Workspace、Session 和 Execution 身份 |
| 任务数量膨胀 | 使用可配置硬上限；当前参数见[配置参考](/docs/reference/configuration-reference.md) |

## 7. 当前能力与后续方向

本文不维护当前工具字段、配置默认值或未来功能清单：

- 当前实现见[私有任务规划架构](/docs/architecture/private-task-planning.md)；
- 配置事实见[配置参考](/docs/reference/configuration-reference.md)；
- 未实现能力见[路线图与已知限制](/docs/product/roadmap-and-known-limitations.md)。