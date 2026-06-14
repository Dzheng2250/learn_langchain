# 配置、领域常量与 Prompt 的管理边界

> 文档状态：Current Design Note
> 本文解释“配置代码如何组织”，不作为完整参数清单。全部环境变量、默认值、单位和调整风险见
> [`configuration-reference.md`](configuration-reference.md)。

## 为什么要优化

程序中出现固定字符串和固定数字并不一定是问题。真正的问题是：一个值的
**归属不明确**，导致多个模块各自复制、修改后彼此不一致。

本项目将固定内容分为三类：

1. **运行配置**：部署者或运维人员可能需要调整，例如维护任务轮询间隔。
2. **领域常量**：描述系统允许存在的业务状态，例如 Execution 的
   `running`、`completed`。
3. **实现细节**：仅服务于一个模块的内部格式，例如一段日志预览长度。

这三类内容不应全部塞入一个全局 `constants.py`，否则只是把混乱移动了位置。

## 本次采用的边界

### 类型化运行配置

`src/config/maintenance.py` 定义 `MaintenanceSettings`。它负责后台维护系统的：

- 轮询间隔；
- 任务租约时长；
- 关闭等待时间；
- 默认重试次数；
- 最大重试退避时间；
- 错误摘要长度。

这些值可以通过环境变量覆盖，并在 Core 服务启动前完成验证。业务组件接收
一个 `MaintenanceSettings` 实例，而不是在内部直接决定策略。

这种做法属于**依赖注入**：组件依赖的是调用方传入的配置对象，因此测试可以
注入小间隔或不同重试策略，生产环境也可以独立调整。

### 领域枚举

以下模块定义稳定的领域词汇：

- `src/core/state/types.py`
  - `ExecutionStatus`
  - `CheckpointState`
- `src/core/maintenance/types.py`
  - `MaintenanceJobType`
  - `MaintenanceStatus`
  - `MaintenancePriority`

`StrEnum` 的值仍然是字符串，因此与 SQLite、JSON 和现有 RPC 响应兼容；但
Python 代码不再需要到处手写容易拼错的字符串。

数据库的新建 Schema 同时使用 `CHECK` 约束拒绝未知状态。旧数据库通过加法
迁移创建等价的校验触发器，因为 SQLite 不能直接给已有列补加 `CHECK`。
代码类型检查和数据库约束形成两道边界：

```text
调用代码 -> Enum 校验 -> Repository -> SQLite CHECK 约束
```

现有旧数据库不会因为本次加法改动被重建。触发器提供相同的非法状态拒绝能力，
但其 Schema 表达形式与新建数据库的 `CHECK` 约束不同。若未来要求两者的表定义
完全一致，应使用显式表重建迁移，而不是在启动时隐式执行。

### Prompt 模块

Prompt 是模型行为策略，不是普通部署配置，也不应混在 Agent 循环控制代码中。

当前 Prompt 放在 `src/core/prompts/`：

- `parent_agent.py`
- `subagent.py`
- `memory_extraction.py`
- `context_summary.py`

Prompt 文件带有版本常量。未来修改模型行为时，可以明确记录 Prompt 版本，
并为构建函数单独编写测试，而不必修改图编排和工具执行逻辑。

## 为什么没有全部移出 `settings.py`

`src/config/settings.py` 仍作为兼容入口。现有模块大量依赖其中的常量，一次性
全部迁移会扩大回归范围，并且难以区分“结构调整”与“行为变化”。

后续应按模块逐步迁移：

1. 建立该模块的类型化配置对象；
2. 由 `CoreApp` 组合根加载并注入；
3. 替换业务模块对全局 `settings.py` 的直接导入；
4. 保留旧名称一段兼容周期；
5. 最后删除不再使用的兼容导出。

## 设计原则

- **单一职责**：配置对象只描述一个功能域的运行策略。
- **依赖倒置**：业务组件接收配置和接口，不自行读取全局环境。
- **开闭原则**：新增维护 Handler 可以注册，不需要修改调度器。
- **组合优于继承**：`CoreApp` 组合配置、Repository、Scheduler 和 Handler。
- **防御性持久化**：领域枚举约束代码，SQLite `CHECK` 约束最终数据。

## 当前边界与后续工作

当前已经完成维护策略、Execution/Checkpoint/Maintenance 状态和主要 Agent
Prompt 的分层。尚未一次性拆分全部 `settings.py`，也未重建旧 SQLite 表。

建议后续优先继续拆分：

1. `ExecutionSettings`
2. `ContextSettings`
3. `MemorySettings`
4. `ToolSettings`
5. `TelemetrySettings`

每次只迁移一个功能域，并通过对应测试确认默认行为保持不变。
